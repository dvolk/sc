import subprocess
import collections

import yaml
import flask

app = flask.Flask(__name__)

config = """
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
"""
config = yaml.safe_load(config)


def get_services(config):
    out = collections.defaultdict(dict)
    for service in config.get("services", []):
        for node in service.get("nodes", []):
            status_lines = subprocess.check_output(
                ["ssh", node, "sudo", "systemctl", "status", service["name"]]
            ).decode()
            out[service["name"]][node] = status_lines
    return out


@app.route("/")
def index():
    print(config)
    s = get_services(config)
    return flask.render_template("index.jinja2", s=s)


if __name__ == "__main__":
    app.run(port=1234, debug=True)
