import subprocess
import collections

import yaml
import flask

app = flask.Flask(__name__)


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


class Services:
    def __init__(self, conf_str):
        self.config = yaml.safe_load(conf_str)
        self.all = []
        self.by_name = collections.defaultdict(list)
        self.by_node = collections.defaultdict(list)
        self._config_changed()

    def _config_changed(self):
        for service_dict in self.config.get("services", []):
            service = Service(service_dict)
            self.all.append(service)
            self.by_name[service.name].append(service)
            for node_name in service.nodes:
                self.by_node[node_name].append(service)

    def update_service_status(self):
        for service in self.all:
            service.update_status_on_all_nodes()


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


@app.route("/")
def index():
    print(services.config)
    services.update_service_status()
    out = make_service_node_dict()
    return flask.render_template("services.jinja2", out=out)


if __name__ == "__main__":
    app.run(port=1234, debug=True)
