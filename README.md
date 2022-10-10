# sc: simple distributed service manager

- manage services on multiple hosts
- one-click deployments and updates
- show service log and open terminal
- memory, load and disk space and service monitoring
- uses just systemd and ssh
- everything in one configuration file
- simple: no "control plane", no agent on worker nodes, no overlay network, no containers, no certificate management, no RBAC, etc
- wrap docker in systemd service (if you must)

## screenshot

<img src="https://i.imgur.com/ECjHDn7.png">

## configuration files

see `test1.yaml`, `test2.yaml`, `test3.yaml` for examples
