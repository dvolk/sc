# sc: simple distributed service manager

- manage services on multiple hosts with a web UI
- one-click deployments and updates
- show service log and open terminal
- memory, load and disk space and service monitoring
- draw a diagram showing service connections and status
- uses just systemd and ssh
- everything in one configuration file
- simple: no "control plane", no agent on worker nodes, no overlay network, no containers, no certificate management, no RBAC, etc
- wrap containers in systemd service (if you must)

## screenshot

<img src="https://i.imgur.com/DvdCaFZ.png">

## Installing and running sc (tested on Ubuntu 22.04)

```bash
apt update
apt install -y python3-pip python3-venv git
git clone https://github.com/dvolk/sc
cd sc
python3 -m venv env
source env/bin/activate
pip3 install argh flask pyyaml flask-socketio simple-websocket
```

Now create a service yaml file, see: `example.yaml`. See below for notes about worker nodes and service configuration.

Run by giving the yaml file as an argument:

```bash
python3 app.py example.yaml
```

There's an optional argument `--term-program`, which can be given your preferred terminal emulator. Its default value is `x-terminal-emulator`, which should use your system terminal emulator. If you set `--term-program` to `xtermjs`, it will use the xtermjs web terminal instead of a local system terminal. This feature is a work in progress and comes with limitations compared to the system terminal. It is based on [xterm.js](https://xtermjs.org/) an [pyxtermjs](https://github.com/cs01/pyxtermjs).

Open browser to http://localhost:1234

## Node requirements

To use `sc` to manage services and deployments, the username running `sc` must be able to ssh into the nodes using the node names as the `root` user, without any authentication or other challenge. This usually just means you need to copy `~/.ssh/id_rsa.pub` to `/root/.ssh/authorized_keys` on the nodes. There are no other prerequisites for nodes, other than those you impose in your deployment scripts.

Using ssh connection multiplexing is highly recommended for performance. This will allow `sc` to use those connections to run commands on the nodes quickly.

Here is an example `.ssh/config`:

```ssh-config
Host *
    ControlMaster auto
    ControlPersist 24h
    ControlPath ~/.ssh/%r@%h:%p
```

## Service configuration

`sc` is configured with a yaml file which contains a list of services. A service is a dictionary.

### Full example

This is a full `sc` configuration example that uses yaml anchors for organisation. It deploys the `catboard` task board with `postgresql` in `docker` on 3 `LXD` nodes. The service is load balanced with `Caddy` used as a reverse proxy. The cloud balancer set up is up to you, or you can just run a single caddy instance. It also shows an optional service diagram that is drawn on the service page with `mermaid.js`.

```yaml
all_nodes: &all_nodes
  - sc-node-5i3O4.lxd
  - sc-node-FrOg2.lxd
  - sc-node-dx5Z0.lxd

postgres_node: &postgres_node
  - sc-node-5i3O4.lxd

catboard_unit: &catboard_unit |
  [Unit]
  Description=Catboard port 7777
  [Service]
  Environment=CATBOARD_SQLALCHEMY_DATABASE_URI=postgresql://postgres:postgres@10.116.104.22:5432/postgres
  WorkingDirectory=/root/catboard
  ExecStart=/root/catboard/env/bin/python /root/catboard/app.py --host 0.0.0.0 --port 7777

catboard_deploy: &catboard_deploy |
  apt update
  apt install -y python3-pip python3-venv git
  rm -rf /root/catboard
  git clone https://github.com/dvolk/catboard /root/catboard
  cd /root/catboard
  python3 -m venv env
  source /root/catboard/env/bin/activate
  pip3 install -r requirements.txt
  CATBOARD_SQLALCHEMY_DATABASE_URI=postgresql://postgres:postgres@10.116.104.22:5432/postgres flask db upgrade

catboard_delete: &catboard_delete |
  rm -rf /root/catboard

caddy_deploy: &caddy_deploy |
  wget -nc https://github.com/caddyserver/caddy/releases/download/v2.6.2/caddy_2.6.2_linux_amd64.deb
  dpkg -i caddy_2.6.2_linux_amd64.deb
  cat > /etc/caddy/Caddyfile <<"EOF"
  :80 {
    basicauth /* {
      user $2a$14$UH3seHGR7r6hqtTF7WQW4eLoNYxNhZajigbWKbkbp48JY5m91ruVi
    }
    reverse_proxy 10.116.104.22:7777 10.116.104.33:7777 10.116.104.8:7777 {
      fail_duration 30s
    }
  }
  EOF
  systemctl restart caddy

caddy_delete: &caddy_delete |
  systemctl stop caddy
  apt purge -y caddy

postgres_docker_unit: &postgres_docker_unit |
  [Unit]
  After=docker.service
  Requires=docker.service
  [Service]
  TimeoutStartSec=0
  Restart=always
  ExecStartPre=-/usr/bin/docker stop %n
  ExecStartPre=-/usr/bin/docker rm %n
  ExecStartPre=/usr/bin/docker pull postgres
  ExecStart=/usr/bin/docker run -p 5432:5432 -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres --rm --name %n postgres
  [Install]
  WantedBy=multi-user.target

postgres_docker_deploy: &postgres_docker_deploy |
  apt update
  apt install -y docker.io

postgres_docker_delete: &postgres_docker_delete |
  docker stop postgres
  docker rm postgres

services:
  - name: caddy
    nodes: *all_nodes
    ports:
      - 80
    deploy: *caddy_deploy
    delete: *caddy_delete

  - name: catboard
    unit: *catboard_unit
    nodes: *all_nodes
    deploy: *catboard_deploy
    delete: *catboard_delete

  - name: docker.postgres
    nodes: *postgres_node
    unit: *postgres_docker_unit
    deploy: *postgres_docker_deploy
    delete: *postgres_docker_delete

mermaid_diagram: |
  graph LR;
  user(user)
  lb[Cloud load-balancer]
  caddy1[caddy sc-node-5i3O4.lxd]
  caddy2[caddy sc-node-FrOg2.lxd]
  caddy3[caddy sc-node-dx5Z0.lxd]
  catboard1[catboard sc-node-5i3O4.lxd]
  catboard2[catboard sc-node-FrOg2.lxd]
  catboard3[catboard sc-node-dx5Z0.lxd]
  postgres[docker.postgres sc-node-5i3O4.lxd]
  subgraph Internet
  user ---> lb
  end
  subgraph Internal network
  lb ---> caddy1 & caddy2 & caddy3 ---> catboard1 & catboard2 & catboard3 ---> postgres
  end
```

### Examples

#### Simple service

A service must have a name, and a list of nodes that it is to be deployed at or is present at. The name must be the same as the systemd unit.

```yaml
services:
  - name: sshd
    nodes:
      - node1
      - node2
```

#### Service with scripts

To deploy, delete and update a deployment, the service must have `deploy` and `delete` keys.

```yaml
  - name: mongod
    unit: |
    nodes:
      - sc-node-5i3O4.lxd
    deploy: |
      wget -nc http://archive.ubuntu.com/ubuntu/pool/main/o/openssl/libssl1.1_1.1.1f-1ubuntu2_amd64.deb
      dpkg -i libssl1.1_1.1.1f-1ubuntu2_amd64.deb
      wget -nc https://repo.mongodb.org/apt/ubuntu/dists/focal/mongodb-org/6.0/multiverse/binary-amd64/mongodb-org-server_6.0.2_amd64.deb
      dpkg -i mongodb-org-server_6.0.2_amd64.deb
      systemctl restart mongod
    delete: |
      systemctl stop mongod
      apt purge -y mongodb-org-server
```

#### Service with scripts and unit file

If the deployment doesn't install a service unit file, you can add a `unit` key to the service dictionary, eg.:

```yaml
services:
  - name: catboard
    unit: |
      [Unit]
      Description=Catboard port 7777
      [Service]
      WorkingDirectory=/root/catboard
      ExecStart=/root/catboard/env/bin/python /root/catboard/app.py --host 0.0.0.0 --port 7777
    nodes:
      - localhost
    ports:
      - 7777
    deploy: |
      apt update
      apt install -y python3-pip python3-venv git
      rm -rf /root/catboard
      git clone https://github.com/dvolk/catboard /root/catboard
      cd /root/catboard
      python3 -m venv env
      source /root/catboard/env/bin/activate
      pip3 install -r requirements.txt
      flask db upgrade
    delete: |
      rm -rf /root/catboard
```

#### Using docker containers

You can use a systemd service to wrap a docker container, for example:

```yaml
services:
  - name: docker.postgres
    nodes:
      - node-v2qg2r
    unit: |
      [Unit]
      After=docker.service
      Requires=docker.service
      [Service]
      TimeoutStartSec=0
      Restart=always
      ExecStartPre=-/usr/bin/docker stop %n
      ExecStartPre=-/usr/bin/docker rm %n
      ExecStartPre=/usr/bin/docker pull postgres
      ExecStart=/usr/bin/docker run -p 5432:5432 -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres --rm --name %n postgres
      [Install]
      WantedBy=multi-user.target
    deploy: |
      apt update
      apt install -y docker.io
    delete: |
      docker stop postgres
      docker rm postgres
```

#### Clickable links for services

If you add a `ports` list to the service dictionary with a list of ports that the service exposes, `sc` will show `http://NODE:PORT` links in the UI.

If the hostnames are not resolvable, you can add them to `/etc/hosts`.
