"""Sc is a distributed service manager."""

import subprocess
import collections
import pathlib
import datetime

import yaml
import flask
import argh

app = flask.Flask(__name__)


def lines_words(text):
    """Return array of lines split into array of words."""
    out = list()
    for line in text.decode().split("\n"):
        out.append(line.split())
    return out


MEM_USED_WARN_PCT: float = 0.45
CPU_LOAD_WARN_PCT: float = 0.5
DISK_USED_WARN_PCT: float = 0.90
ACKNOWLEDGED_ALERTS: set[str] = set()


class Node:
    """Class encapsulating a worker node for purpose of collecting metrics."""

    def __init__(self, node_name):
        """Initialize class variables."""
        self.node_name = node_name
        self.mem_used = 0
        self.mem_avail = 0
        self.mem_warn = 0
        self.load = 0
        self.cpus = 0
        self.cpu_warn = 0
        self.df = []
        self.is_up = True
        self.warnings = 0

    def update_metrics(self):
        """Update worker node metrics by running commands over ssh."""
        self.is_up = True
        mem_cmd = ["ssh", "-oConnectTimeout=3", "root@" + self.node_name, "free"]
        try:
            mem_cmd_out_words = lines_words(subprocess.check_output(mem_cmd))
        except subprocess.CalledProcessError:
            self.is_up = False
            self.warnings += 1
            return
        self.mem_used = int(int(mem_cmd_out_words[1][2]) // 1e3)
        self.mem_avail = int(int(mem_cmd_out_words[1][1]) // 1e3)
        load_cmd = ["ssh", "root@" + self.node_name, "uptime"]
        load_cmd_out_words = lines_words(subprocess.check_output(load_cmd))
        self.load = float(load_cmd_out_words[0][-3][:-1])
        cpus_cmd = ["ssh", "root@" + self.node_name, "cat /proc/cpuinfo"]
        cpus_cmd_out_words = subprocess.check_output(cpus_cmd).decode().split()
        self.cpus = cpus_cmd_out_words.count("vendor_id")
        df_cmd = ["ssh", "root@" + self.node_name, "df"]
        df_out_words = lines_words(subprocess.check_output(df_cmd))[1:-1]
        self.mem_warn = False
        if int(self.mem_used) > MEM_USED_WARN_PCT * int(self.mem_avail):
            self.mem_warn = True
            if not is_node_alert_acked(self.node_name, "mem"):
                self.warnings += 1
        self.cpu_warn = False
        if float(self.load) > CPU_LOAD_WARN_PCT * int(self.cpus):
            self.cpu_warn = True
            if not is_node_alert_acked(self.node_name, "cpu_load"):
                self.warnings += 1
        for df_data in df_out_words:
            device = df_data[0]
            mounted_on = " ".join(df_data[5:])
            used_gb = int(df_data[2]) / 1000000
            avail_gb = int(df_data[3]) / 1000000
            total_gb = used_gb + avail_gb
            percent_used = used_gb / total_gb
            if not (
                device.startswith("/dev/sd")
                or device.startswith("/dev/mapper")
                or device.startswith("/dev/vd")
            ):
                continue
            warn = percent_used > DISK_USED_WARN_PCT and avail_gb < 10
            self.df.append(
                {
                    "mounted_on": mounted_on,
                    "used_gb": used_gb,
                    "total_gb": total_gb,
                    "percent_used": percent_used,
                    "warn": warn,
                }
            )
            if warn:
                mounted_on_nice = mounted_on.replace("/", "-")
                if not is_node_alert_acked(self.node_name, mounted_on_nice):
                    self.warnings += 1


class Nodes:
    """Class for storing a collection of worker nodes."""

    def __init__(self, node_names):
        """Initialize class variables."""
        self.nodes = []
        self.warnings = 0
        self.total_mem_used = 0
        self.total_mem_avail = 0
        self.total_load = 0
        self.total_cpus = 0
        self.total_df_used_gb = 0
        self.total_df_total_gb = 0
        for node_name in node_names:
            self.nodes.append(Node(node_name))

    def update(self):
        """Update metrics on all nodes."""
        self.warnings = 0
        for node in self.nodes:
            node.update_metrics()
            self.warnings += node.warnings
            self.total_mem_used += node.mem_used
            self.total_mem_avail += node.mem_avail
            self.total_load += node.load
            self.total_cpus += node.cpus
            self.total_df_used_gb += sum([disk["used_gb"] for disk in node.df])
            self.total_df_total_gb += sum([disk["total_gb"] for disk in node.df])


class Service:
    """Class encapsulating a service (accross all worker nodes)."""

    def __init__(self, service_dict):
        """Initialize class variables."""
        self.name = service_dict["name"]
        self.nodes = service_dict.get("nodes", [])
        self.deploy_script = service_dict.get("deploy", None)
        self.delete_script = service_dict.get("delete", None)
        self.systemd_unit = service_dict.get("unit", None)
        self.ports = service_dict.get("ports", None)
        self.status = dict()

    def update_status_on_node(self, node_name):
        """Update the service status on a node by running systemctl status."""
        cmd = [
            "ssh",
            "root@" + node_name,
            "systemctl",
            "--no-page",
            "status",
            self.name,
        ]
        p = subprocess.run(cmd, stdout=subprocess.PIPE)
        if p.returncode == 0:
            self.status[node_name] = "active"
        elif p.returncode == 3:
            self.status[node_name] = "inactive"
        else:
            self.status[node_name] = "unknown"

    def update_status_on_all_nodes(self):
        """Update service status on all nodes."""
        for node_name in self.nodes:
            self.update_status_on_node(node_name)

    def start(self, node_name):
        """Start service on node by running systemctl start."""
        cmd = ["ssh", "root@" + node_name, "systemctl", "start", self.name]
        subprocess.check_output(cmd)

    def stop(self, node_name):
        """Stop service on node by running systemctl stop."""
        cmd = ["ssh", "root@" + node_name, "systemctl", "stop", self.name]
        subprocess.check_output(cmd)

    def restart(self, node_name):
        """Restart service on node by running systemctl restart."""
        cmd = ["ssh", "root@" + node_name, "systemctl", "restart", self.name]
        subprocess.check_output(cmd)

    def open_terminal_shell(self, node_name):
        """Open a terminal shell on a node."""
        cmd = f"ssh root@{node_name}"
        term_cmd = ["x-terminal-emulator", "-e", cmd]
        subprocess.Popen(term_cmd)

    def open_terminal_log(self, node_name):
        """Open a terminal with the systemd service log on a node."""
        cmd = f"ssh root@{node_name} journalctl -fu {self.name}"
        term_cmd = ["x-terminal-emulator", "-e", cmd]
        subprocess.Popen(term_cmd)

    def deploy(self, node_name):
        """Run deploy script for service on node."""
        if self.systemd_unit:
            with open(f"/tmp/{self.name}.service", "w") as f:
                f.write(self.systemd_unit)
            cmd = [
                "scp",
                f"/tmp/{self.name}.service",
                f"root@{node_name}:/lib/systemd/system/{self.name}.service",
            ]
            subprocess.check_output(cmd)
            cmd = ["ssh", "root@" + node_name, "systemctl daemon-reload"]
            subprocess.check_output(cmd)
        with open(f"/tmp/{self.name}.deploy.sh", "w") as f:
            f.write("set -x\n\n")
            f.write(self.deploy_script)
        cmd = [
            "scp",
            f"/tmp/{self.name}.deploy.sh",
            f"root@{node_name}:/tmp/sc.{self.name}.deploy.sh",
        ]
        subprocess.check_output(cmd)
        cmd = [
            "ssh",
            "root@" + node_name,
            f"bash /tmp/sc.{self.name}.deploy.sh > /tmp/{self.name}.deploy.stdout &",
        ]
        print(cmd)
        subprocess.check_output(cmd)
        if self.systemd_unit:
            cmd = ["ssh", "root@" + node_name, f"systemctl start {self.name}.service"]
            subprocess.check_output(cmd)

    def delete(self, node_name):
        """Run delete deployment script for service on node."""
        if self.systemd_unit:
            cmd = ["ssh", "root@" + node_name, f"systemctl stop {self.name}.service"]
            subprocess.run(cmd)
            cmd = [
                "ssh",
                "root@" + node_name,
                f"rm /lib/systemd/system/{self.name}.service",
            ]
            subprocess.run(cmd)
            cmd = ["ssh", "root@" + node_name, "systemctl daemon-reload"]
            subprocess.check_output(cmd)
        with open(f"/tmp/{self.name}.delete.sh", "w") as f:
            f.write("set -x\n\n")
            f.write(self.delete_script)
        cmd = [
            "scp",
            f"/tmp/{self.name}.delete.sh",
            f"root@{node_name}:/tmp/sc.{self.name}.delete.sh",
        ]
        subprocess.check_output(cmd)
        cmd = [
            "ssh",
            "root@" + node_name,
            f"bash /tmp/sc.{self.name}.delete.sh > /tmp/{self.name}.delete.stdout &",
        ]
        print(cmd)
        subprocess.check_output(cmd)

    def update(self, node_name):
        """Update service on node by running delete and then deploy."""
        self.delete(node_name)
        self.deploy(node_name)


class Services:
    """Class encapsulating a collection of services."""

    def __init__(self, conf_str):
        """Initialize class variables."""
        self.config = yaml.safe_load(conf_str)
        self.all = []
        self.by_name = dict()
        self.by_node = collections.defaultdict(list)
        self.warnings = 0
        self._config_changed()

    def _config_changed(self):
        """Update class variables to be done when the config changes."""
        for service_dict in self.config.get("services", []):
            service = Service(service_dict)
            self.all.append(service)
            self.by_name[service.name] = service
            for node_name in service.nodes:
                self.by_node[node_name].append(service)

    def update_service_status(self):
        """Update services status on all nodes."""
        self.warnings = 0
        for service in self.all:
            service.update_status_on_all_nodes()
            for node_name, status in service.status.items():
                print(node_name, status)
                if status != "active":
                    if not is_service_alert_acked(service.name, node_name):
                        self.warnings += 1

    def get_node_names(self):
        """Return all node names."""
        return self.by_node.keys()


services = None
search_filter = None


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
    """Make a dict[dict] to be used by the dashboard."""
    out = collections.defaultdict(dict)
    for service in services.all:
        for node_name in service.nodes:
            out[service.name][node_name] = service
    return out


@app.route("/start/<service>/<node_name>")
def start(service, node_name):
    """Start service on node endpoint."""
    services.by_name[service].start(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/stop/<service>/<node_name>")
def stop(service, node_name):
    """Stop service on node endpoint."""
    services.by_name[service].stop(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/restart/<service>/<node_name>")
def restart(service, node_name):
    """Restart service on node endpoint."""
    services.by_name[service].restart(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/open_terminal_log/<service>/<node_name>")
def open_terminal_log(service, node_name):
    """Open terminal log on node endpoint."""
    services.by_name[service].open_terminal_log(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/open_terminal_shell/<service>/<node_name>")
def open_terminal_shell(service, node_name):
    """Open terminal shell on node endpoint."""
    services.by_name[service].open_terminal_shell(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/deploy/<service>/<node_name>")
def deploy(service, node_name):
    """Deploy service on node endpoint."""
    services.by_name[service].deploy(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/delete/<service>/<node_name>")
def delete(service, node_name):
    """Delete service on node endpoint."""
    services.by_name[service].delete(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/update/<service>/<node_name>")
def update(service, node_name):
    """Update service on node endpoint."""
    services.by_name[service].update(node_name)
    return flask.redirect(flask.url_for("index"))


@app.route("/apply_settings", methods=["POST"])
def apply_settings():
    """Save dashboard page settings endpoint."""
    print(flask.request.form)
    if flask.request.form.get("Submit") == "Submit_apply":
        global refresh_rate
        refresh_rate = flask.request.form.get("refresh_rate")
    if flask.request.form.get("Submit") == "Submit_search":
        global search_filter
        search_filter = flask.request.form.get("search_filter").strip()
        print(search_filter)
        print(type(search_filter))
    return flask.redirect(flask.url_for("index"))


@app.route("/")
def index():
    """Dashboard index endpoint."""
    global services
    services = Services(pathlib.Path(services_yaml).read_text())
    services.update_service_status()
    out = make_service_node_dict()
    nodes = Nodes(services.get_node_names())
    nodes.update()
    return flask.render_template(
        "services.jinja2",
        services=services,
        out=out,
        nodes=nodes,
        refresh_rate=refresh_rate,
        search_filter=search_filter,
    )


@app.route("/toggle_acknowledge_alert/<service_name>/<node_name>/<node_alert_type>")
def toggle_acknowledge_alert(service_name, node_name, node_alert_type):
    """Endpoint to toggle alert.

    If the argument does not apply to the alert type, pass '-' for that argument.
    """
    elem = service_name + node_name + node_alert_type
    if elem in ACKNOWLEDGED_ALERTS:
        ACKNOWLEDGED_ALERTS.remove(elem)
    else:
        ACKNOWLEDGED_ALERTS.add(elem)
    return flask.redirect(flask.url_for("index"))


def is_service_alert_acked(service_name, node_name):
    """Return if the service alert is acknowledged."""
    return service_name + node_name + "-" in ACKNOWLEDGED_ALERTS


def is_node_alert_acked(node_name, node_alert_type):
    """Return if the node alert is acknowledged."""
    return "-" + node_name + node_alert_type in ACKNOWLEDGED_ALERTS


def is_ok_config(sc_config):
    """Check if the argument string is a valid config."""
    try:
        yaml.safe_load(sc_config)
        return True
    except Exception as e:
        print(e)
        return False


@app.route("/config", methods=["GET", "POST"])
def config():
    """Config view/post endpoint."""
    global services_yaml
    if flask.request.method == "GET":
        sc_config = pathlib.Path(services_yaml).read_text()

        return flask.render_template(
            "config.jinja2",
            sc_config=sc_config,
        )
    if flask.request.method == "POST":
        print(flask.request.form)
        if flask.request.form.get("Submit") == "Submit_cancel":
            return flask.redirect(flask.url_for("index"))
        if flask.request.form.get("Submit") == "Submit_save":
            unsafe_sc_config = flask.request.form.get("new_config")
            time_now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            with open(f"./config_{time_now}.yaml", "w") as f:
                f.write(unsafe_sc_config)
            if not is_ok_config(unsafe_sc_config):
                return flask.redirect(flask.url_for("config"))
            services_yaml = f"./config_{time_now}.yaml"
            return flask.redirect(flask.url_for("index"))


def main(services_yaml_):
    """Start sc web service."""
    global services_yaml
    services_yaml = services_yaml_
    app.run(port=1234, debug=True)


if __name__ == "__main__":
    global refresh_rate
    refresh_rate = ""
    argh.dispatch_command(main)
