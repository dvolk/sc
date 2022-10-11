# sc: simple distributed service manager

- manage services on multiple hosts with a web UI
- one-click deployments and updates
- show service log and open terminal
- memory, load and disk space and service monitoring
- uses just systemd and ssh
- everything in one configuration file
- simple: no "control plane", no agent on worker nodes, no overlay network, no containers, no certificate management, no RBAC, etc
- wrap docker in systemd service (if you must)

## screenshot

<img src="https://i.imgur.com/ECjHDn7.png">

## Installing and running sc (Ubuntu 22.04)

```
apt update
apt install -y python3-pip python3-venv git
git clone https://github.com/dvolk/sc
cd sc
python3 -m venv env
source env/bin/activate
pip3 install argh flask pyyaml
```

Now create a service yaml file, see `test1.yaml`, `test2.yaml` and `test3.yaml` for examples. See below for notes about worker nodes and service configuration.

Run by giving the yaml file as an argument:

```
python3 app.py yourservices.yaml
```

Open browser to http://localhost:1234

## Node requirements

To use `sc` to manage services and deployments, the username running `sc` must be able to ssh into the nodes using the node names as the `root` user, without any authentication or other challenge. This usually just means you need to copy `~/.ssh/id_rsa.pub` to `/root/.ssh/authorized_keys` on the nodes. There are no other prerequisites for nodes, other than those you impose in your deployment scripts.

Using ssh connection multiplexing is recommended for performance. This will allow `sc` to use those connections to run commands on the nodes quickly.

Here is an example `.ssh/config`:

```
Host *
    ControlMaster auto
    ControlPersist 24h
    ControlPath ~/.ssh/%r@%h:%p

Host sc-node-5i3O4.lxd
    Hostname 10.116.104.22
    User root

Host sc-node-FrOg2.lxd
    Hostname 10.116.104.33
    User root

Host sc-node-dx5Z0.lxd
    Hostname 10.116.104.8
    User root
```

## Service configuration

`sc` is configured with a yaml file which contains a list of services. A service is a dictionary.

A service must have a name, and a list of nodes that it is to be deployed at or is present at. The name must be the same as the systemd unit.

### Examples

#### Simple service

```
services:
  - name: sshd
    nodes:
      - node1
      - node2
```

#### Service with scripts

To deploy, delete and update a deployment, the service must have `deploy` and `delete` keys.

```
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

```
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

```
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
