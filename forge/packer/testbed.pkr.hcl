packer {
  required_plugins {
    docker = {
      version = ">= 1.0.8"
      source  = "github.com/hashicorp/docker"
    }
    ansible = {
      version = ">= 1.1.0"
      source  = "github.com/hashicorp/ansible"
    }
  }
}

source "docker" "testbed" {
  image   = "debian:bookworm-slim"
  commit  = true
  changes = [
    "ENTRYPOINT [\"/entrypoint.sh\"]",
    "EXPOSE 18443 18888"
  ]
}

build {
  name    = "check_snclient-testbed"
  sources = ["source.docker.testbed"]

  # Minimal packages for Ansible, OpenSSL, Tinyproxy and networking tools
  provisioner "shell" {
    environment_vars = [
      "DEBIAN_FRONTEND=noninteractive",
    ]
    inline = [
      "apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates curl openssl procps binutils tinyproxy && rm -rf /var/lib/apt/lists/*",
    ]
  }

  # Re-use our canonical Ansible playbook to bake the image
  provisioner "ansible" {
    command         = "${path.root}/../.venv/bin/ansible-playbook"
    playbook_file   = "${path.root}/../ansible/playbook.yml"
    user            = "root"
    extra_arguments = [
      "-e", "workdir=/var/run/testbed",
      "-e", "bin_dir=/usr/bin",
      "-e", "snclient_port=18443",
      "-e", "tinyproxy_port=18888",
    ]
  }

  post-processor "docker-tag" {
    repository = "check_snclient-testbed"
    tags       = ["latest"]
  }
}
