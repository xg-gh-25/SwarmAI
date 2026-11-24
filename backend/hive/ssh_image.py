"""The SSH-machine Hive image and the scripts the Host runs on the machine (design
docs/design/2026-11-24-swarm-out-ssh-machine.md §5, decision D2). Kept as text here, as
hive/user_data.py keeps a Hive's first-boot script: the Host's daemon is a packaged binary
that carries Python modules, not repository files, and it sends these to the machine over
SSH (hive/ssh_provisioner).
"""
from __future__ import annotations

#: the image, built on the machine from a context of this file and the checked package
DOCKERFILE = r"""# The SSH-machine Hive image (design docs/design/2026-11-24-swarm-out-ssh-machine.md §5.1, D2):
# the Hive release package installed the way hive/agentcore/Dockerfile installs it, on
# Amazon Linux 2023, so it runs on a machine whose own system is too old for the Hive's
# dependencies (an Amazon Linux 2 Cloud Desktop: glibc 2.26). Differences from the
# AgentCore image:
#
# - the package stays root's in the image (/opt/swarmai): the Hive runs as the person's own
#   uid, the same uid as the tasks it runs, so inside the container a task cannot edit the
#   Hive's own gate code (no docker socket in the container either). This is not a wall
#   around the machine: the person's home is mounted writable, so a task can leave code
#   there that runs later outside the container (a shell start-up file run at the person's
#   next login or a Host command over ssh), as the person, who can use docker. A task on
#   this machine acts as the person (design §7); `general_tasks` decides which tasks come;
# - a user with the person's uid, gid, name and home (build arguments): ssh refuses to run
#   for a uid with no passwd entry ("No user exists for uid"), and the person's $HOME is
#   mounted at the same path;
# - openssh-clients (git over SSH) and libxcrypt-compat (libcrypt.so.1, which tools mounted
#   from the machine often need), iproute (ss);
# - no supervisor and no exposed port: the container runs on the machine's network and the
#   Hive binds 127.0.0.1, reached by the Host through its SSH tunnel (core/carriers/ssh_tunnel).
#
# Built by hive/ssh_provisioner on the machine, from a context holding only this file and
#   swarmai-hive.tar.gz  the release package, checked against its checksums.txt by the Host.
# COPY rather than a BuildKit bind mount: the machine's docker may not run BuildKit.
FROM public.ecr.aws/amazonlinux/amazonlinux:2023

RUN dnf install -y python3.12 python3.12-pip python3.12-devel nodejs20 npm git gcc make \
        tar gzip shadow-utils procps-ng util-linux iproute findutils which hostname \
        openssh-clients libxcrypt-compat \
    && dnf clean all && rm -rf /var/cache/dnf

ARG HIVE_UID
ARG HIVE_GID
ARG HIVE_USER
ARG HIVE_HOME
RUN (getent group "${HIVE_GID}" >/dev/null || groupadd -g "${HIVE_GID}" hive) \
    && useradd -M -u "${HIVE_UID}" -g "${HIVE_GID}" -d "${HIVE_HOME}" -s /bin/bash "${HIVE_USER}"

COPY swarmai-hive.tar.gz /tmp/hive.tar.gz
RUN mkdir -p /opt/swarmai \
    && tar xzf /tmp/hive.tar.gz --strip-components=1 -C /opt/swarmai \
    && rm -f /tmp/hive.tar.gz \
    && bash /opt/swarmai/hive/ensure-node.sh

WORKDIR /opt/swarmai/backend
RUN python3.12 -m venv .venv \
    && .venv/bin/pip install -q --upgrade pip \
    && .venv/bin/pip install -q -e . \
    && (.venv/bin/python scripts/bundle_tree_sitter_grammars.py generated/tree_sitter_grammars --deadline 120 \
        || echo "WARN: tree-sitter grammars not bundled; code intel uses its regex path") \
    && rm -rf /root/.cache/pip

USER ${HIVE_UID}:${HIVE_GID}
# The environment file (hive/ssh_provisioner) sets SWARMAI_MODE=hive, SWARMAI_CARRIER=ssh,
# SWARMAI_PORT, SWARM_DATA_DIR, the bridge identity, AWS_PROFILE and the Hive's credential.
CMD ["sh", "-c", "exec /opt/swarmai/backend/.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port \"${SWARMAI_PORT:-18321}\" --log-level info"]
"""
