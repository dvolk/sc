import subprocess
import collections
import pathlib

import yaml
import flask
import argh

app = flask.Flask(__name__)


def lines_words(text):
    out = list()
    for line in text.decode().split("\n"):
        out.append(line.split())
    return out


class Node:
    def __init__(self, node_name):
        self.node_name = node_name
        self.mem_used = None
        self.mem_avail = None
        self.mem_warn = None
        self.load = None
        self.cpus = None
        self.cpu_warn = None
        self.df = []
        self.is_up = None
        self.warnings = 0

    def update_metrics(self):
        self.is_up = True
        mem_cmd = ["ssh", self.node_name, "free"]
        try:
            mem_cmd_out_words = lines_words(subprocess.check_output(mem_cmd))
        except subprocess.CalledProcessError:
            self.is_up = False
            self.warnings += 1
            return
        self.mem_used = int(int(mem_cmd_out_words[1][2]) // 1e3)
        self.mem_avail = int(int(mem_cmd_out_words[1][1]) // 1e3)
        load_cmd = ["ssh", self.node_name, "uptime"]
        load_cmd_out_words = lines_words(subprocess.check_output(load_cmd))
        print(load_cmd_out_words)
        self.load = float(load_cmd_out_words[0][-3][:-1])
        cpus_cmd = ["ssh", self.node_name, "cat /proc/cpuinfo"]
        cpus_cmd_out_words = subprocess.check_output(cpus_cmd).decode().split()
        self.cpus = cpus_cmd_out_words.count("vendor_id")
        df_cmd = ["ssh", self.node_name, "df"]
        df_out_words = lines_words(subprocess.check_output(df_cmd))[1:-1]
        self.mem_warn = False
        if int(self.mem_used) > 0.45 * int(self.mem_avail):
            self.mem_warn = True
            self.warnings += 1
        self.cpu_warn = False
        if float(self.load) > 0.75 * int(self.cpus):
            self.cpu_warn = True
            self.warnings += 1
        for df_data in df_out_words:
            device = df_data[0]
            mounted_on = " ".join(df_data[5:])
            used_gb = int(df_data[2]) / 1000000
            avail_gb = int(df_data[3]) / 1000000
            total_gb = used_gb + avail_gb
            percent_used = (used_gb / total_gb) * 100
            if not (device.startswith("/dev/sd") or device.startswith("/dev/mapper")):
                continue
            self.df.append(
                {
                    "mounted_on": mounted_on,
                    "used_gb": used_gb,
                    "total_gb": total_gb,
                    "percent_used": percent_used,
                    "warn": percent_used > 75,
                }
            )
            warn = percent_used > 75
            if warn:
                self.warnings += 1


class Nodes:
    def __init__(self, node_names):
        self.nodes = []
        self.warnings = 0
        for node_name in node_names:
            self.nodes.append(Node(node_name))

    def update(self):
        self.warnings = 0
        for node in self.nodes:
            node.update_metrics()
            self.warnings += node.warnings


class Service:
    def __init__(self, service_dict):
        self.name = service_dict["name"]
        self.nodes = service_dict.get("nodes", [])
        self.deploy_script = service_dict.get("deploy", None)
        self.delete_script = service_dict.get("delete", None)
        self.systemd_unit = service_dict.get("unit", None)
        self.ports = service_dict.get("ports", None)
        self.status = dict()

    def update_status_on_node(self, node_name):
        cmd = ["ssh", node_name, "systemctl", "--no-page", "status", self.name]
        p = subprocess.run(cmd, stdout=subprocess.PIPE)
        if p.returncode == 0:
            self.status[node_name] = "active"
        elif p.returncode == 3:
            self.status[node_name] = "inactive"
        else:
            self.status[node_name] = "unknown"

    def update_status_on_all_nodes(self):
        for node_name in self.nodes:
            self.update_status_on_node(node_name)

    def restart(self, node_name):
        cmd = ["ssh", node_name, "systemctl", "restart", self.name]
        subprocess.check_output(cmd)

    def stop(self, node_name):
        cmd = ["ssh", node_name, "systemctl", "stop", self.name]
        subprocess.check_output(cmd)

    def start(self, node_name):
        cmd = ["ssh", node_name, "systemctl", "start", self.name]
        subprocess.check_output(cmd)

    def open_terminal_shell(self, node_name):
        cmd = f"ssh {node_name}"
        term_cmd = ["x-terminal-emulator", "-e", cmd]
        subprocess.Popen(term_cmd)

    def open_terminal_log(self, node_name):
        cmd = f"ssh {node_name} journalctl -fu {self.name}"
        term_cmd = ["x-terminal-emulator", "-e", cmd]
        subprocess.Popen(term_cmd)

    def deploy(self, node_name):
        """Run deploy script, if it exists in the config file."""
        with open(f"/tmp/{self.name}.service", "w") as f:
            f.write(self.systemd_unit)
        cmd = [
            "scp",
            f"/tmp/{self.name}.service",
            f"{node_name}:/lib/systemd/system/{self.name}.service",
        ]
        subprocess.check_output(cmd)
        cmd = ["ssh", node_name, "systemctl daemon-reload"]
        subprocess.check_output(cmd)
        with open(f"/tmp/{self.name}.deploy.sh", "w") as f:
            f.write(self.deploy_script)
        cmd = [
            "scp",
            f"/tmp/{self.name}.deploy.sh",
            f"{node_name}:/tmp/sc.{self.name}.deploy.sh",
        ]
        subprocess.check_output(cmd)
        cmd = [
            "ssh",
            node_name,
            f"bash /tmp/sc.{self.name}.deploy.sh > /tmp/{self.name}.deploy.stdout &",
        ]
        print(cmd)
        subprocess.check_output(cmd)
        cmd = ["ssh", node_name, f"systemctl start {self.name}.service"]
        subprocess.check_output(cmd)

    def delete(self, node_name):
        # run delete script if it exists
        cmd = ["ssh", node_name, f"systemctl stop {self.name}.service"]
        subprocess.run(cmd)
        cmd = ["ssh", node_name, f"rm /lib/systemd/system/{self.name}.service"]
        subprocess.run(cmd)
        cmd = ["ssh", node_name, "systemctl daemon-reload"]
        subprocess.check_output(cmd)
        with open(f"/tmp/{self.name}.delete.sh", "w") as f:
            f.write(self.delete_script)
        cmd = [
            "scp",
            f"/tmp/{self.name}.delete.sh",
            f"{node_name}:/tmp/sc.{self.name}.delete.sh",
        ]
        subprocess.check_output(cmd)
        cmd = [
            "ssh",
            node_name,
            f"bash /tmp/sc.{self.name}.delete.sh > /tmp/{self.name}.delete.stdout &",
        ]
        print(cmd)
        subprocess.check_output(cmd)

    def update(self, node_name):
        self.delete()
        self.deploy()


class Services:
    def __init__(self, conf_str):
        self.config = yaml.safe_load(conf_str)
        self.all = []
        self.by_name = dict()
        self.by_node = collections.defaultdict(list)
        self.warnings = 0
        self._config_changed()

    def _config_changed(self):
        for service_dict in self.config.get("services", []):
            service = Service(service_dict)
            self.all.append(service)
            self.by_name[service.name] = service
            for node_name in service.nodes:
                self.by_node[node_name].append(service)

    def update_service_status(self):
        self.warnings = 0
        for service in self.all:
            service.update_status_on_all_nodes()
            self.warnings += list(service.status.values()).count("inactive") + list(
                service.status.values()
            ).count("unknown")

    def get_node_names(self):
        return self.by_node.keys()


services = None


def icon(name):
    """Format html for fontawesome icons."""
    return f'<i class="fa fa-{name} fa-fw"></i>'


@app.context_processor
def inject_globals():
    """Add some stuff into all templates."""
    return {
        "icon": icon,
    }


def make_service_node_dict():
    out = collections.defaultdict(dict)
    for service in services.all:
        print(service.nodes)
        for node_name in service.nodes:
            out[service.name][node_name] = service
    print(out)
    return out


@app.route("/restart/<service>/<node_name>")
def restart(service, node_name):
    services.by_name[service].restart(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/stop/<service>/<node_name>")
def stop(service, node_name):
    services.by_name[service].stop(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/start/<service>/<node_name>")
def start(service, node_name):
    services.by_name[service].start(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/open_terminal_log/<service>/<node_name>")
def open_terminal_log(service, node_name):
    services.by_name[service].open_terminal_log(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/open_terminal_shell/<service>/<node_name>")
def open_terminal_shell(service, node_name):
    services.by_name[service].open_terminal_shell(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/deploy/<service>/<node_name>")
def deploy(service, node_name):
    services.by_name[service].deploy(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/delete/<service>/<node_name>")
def delete(service, node_name):
    services.by_name[service].delete(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/update/<service>/<node_name>")
def update(service, node_name):
    services.by_name[service].update(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/")
def index():
    print(services.config)
    services.update_service_status()
    out = make_service_node_dict()
    nodes = Nodes(services.get_node_names())
    nodes.update()
    print(nodes.nodes[0].__dict__)
    print(nodes.warnings)
    return flask.render_template(
        "services.jinja2",
        services=services,
        out=out,
        nodes=nodes,
    )


def main(services_yaml):
    global services
    services = Services(pathlib.Path(services_yaml).read_text())
    app.run(port=1234, debug=True)


if __name__ == "__main__":
    argh.dispatch_command(main)
