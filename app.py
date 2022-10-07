import subprocess
import collections

import yaml
import flask

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
        self.load = None
        self.cpus = None

    def update_metrics(self):
        mem_cmd = ["ssh", self.node_name, "free"]
        mem_cmd_out_words = lines_words(subprocess.check_output(mem_cmd))
        self.mem_used = int(int(mem_cmd_out_words[1][2]) // 1e3)
        self.mem_avail = int(int(mem_cmd_out_words[1][1]) // 1e3)
        load_cmd = ["ssh", self.node_name, "uptime"]
        load_cmd_out_words = lines_words(subprocess.check_output(load_cmd))
        self.load = float(load_cmd_out_words[0][10][:-1])
        cpus_cmd = ["ssh", self.node_name, "cat /proc/cpuinfo"]
        cpus_cmd_out_words = subprocess.check_output(cpus_cmd).decode().split()
        self.cpus = cpus_cmd_out_words.count("vendor_id")


class Nodes:
    def __init__(self, node_names):
        self.nodes = []
        for node_name in node_names:
            self.nodes.append(Node(node_name))

    def update(self):
        for node in self.nodes:
            node.update_metrics()


class Service:
    def __init__(self, service_dict):
        self.name = service_dict["name"]
        self.nodes = service_dict.get("nodes", [])
        self.systemd_status = None

    def parse_systemd_status(self):
        lines = self.systemd_status.split("\n")
        self.status = lines[2].split()[1]

    def update_status_on_node(self, node_name):
        cmd = ["ssh", node_name, "systemctl", "--no-page", "status", self.name]
        self.systemd_status = subprocess.check_output(cmd).decode()
        self.parse_systemd_status()

    def update_status_on_all_nodes(self):
        for node_name in self.nodes:
            self.update_status_on_node(node_name)

    def restart(self, node_name):
        cmd = ["ssh", node_name, "systemctl", "restart", self.name]
        self.systemd_status = subprocess.check_output(cmd)

    def stop(self, node_name):
        cmd = ["ssh", node_name, "systemctl", "stop", self.name]
        self.systemd_status = subprocess.check_output(cmd)

    def start(self, node_name):
        cmd = ["ssh", node_name, "systemctl", "start", self.name]
        self.systemd_status = subprocess.check_output(cmd)

    def open_terminal_shell(self, node_name):
        cmd = f"ssh {node_name}"
        term_cmd = ["x-terminal-emulator", "-e", cmd]
        subprocess.Popen(term_cmd)

    def open_terminal_log(self, node_name):
        cmd = f"ssh {node_name} journalctl -fu {self.name}"
        term_cmd = ["x-terminal-emulator", "-e", cmd]
        subprocess.Popen(term_cmd)

    def deploy(self, node_name):
        # run deploy script if it exists
        pass

    def delete(self, node_name):
        # run delete script if it exists
        pass

    def update(self, node_name):
        # delete + deploy?
        pass


class Services:
    def __init__(self, conf_str):
        self.config = yaml.safe_load(conf_str)
        self.all = []
        self.by_name = dict()
        self.by_node = collections.defaultdict(list)
        self._config_changed()

    def _config_changed(self):
        for service_dict in self.config.get("services", []):
            service = Service(service_dict)
            self.all.append(service)
            self.by_name[service.name] = service
            for node_name in service.nodes:
                self.by_node[node_name].append(service)

    def update_service_status(self):
        for service in self.all:
            service.update_status_on_all_nodes()

    def get_node_names(self):
        return self.by_node.keys()


services = Services(
    """
services:
  - name: sshd
    nodes:
      - eclipse
      - fanubuntu
    deploy: |
      git clone https://github.com/dvolk/catboard
      cd catboard
      virtualenv env
      source env/bin/activate
      pip3 install -r requirements.txt
      flask db upgrade
      python3 app.py
    delete: |
      rm -rf .*
  - name: mongodb
    nodes:
      - eclipse
  - name: ModemManager
    nodes:
      - eclipse
      - fanubuntu
  - name: daaas-wfe
    nodes:
      - pl_dev_dev_wfe
"""
)


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
            print(service.systemd_status)
            print(node_name)
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
    return flask.render_template("services.jinja2", out=out, nodes=nodes)


if __name__ == "__main__":
    app.run(port=1234, debug=True)
