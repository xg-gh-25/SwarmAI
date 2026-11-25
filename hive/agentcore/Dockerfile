# syntax=docker/dockerfile:1
# The elastic Hive image: one AgentCore Runtime microVM per Swarm Out run (design
# docs/design/2026-11-20-swarm-out-multi-carrier.md §6.3). The Hive release package installed
# the way a Hive's first boot installs it (backend/hive/user_data.py steps 1-4), without
# Caddy: AgentCore's session endpoint is the outer auth layer, hive_auth the inner one.
#
# Built by hive/agentcore/build_image.sh, whose build context holds only:
#   swarmai-hive.tar.gz  the release package (dist/swarmai-hive-v<version>-linux-arm64.tar.gz)
#   supervisor.py        hive/agentcore/supervisor.py
FROM public.ecr.aws/amazonlinux/amazonlinux:2023

# Step 1 of user_data.py, plus what the image lacks that an EC2 AMI has (sudo and acl:
# Feature Map replay starts its processes as the replay user with sudo, and gives that user
# its sandbox folder with setfacl; hive/replay-host.sh below).
RUN dnf install -y python3.12 python3.12-pip python3.12-devel nodejs20 npm git gcc make \
        tar gzip shadow-utils procps-ng util-linux sudo acl \
    && dnf clean all && rm -rf /var/cache/dnf

RUN useradd -m -s /bin/bash swarm \
    && mkdir -p /home/swarm/.swarm-ai/logs /opt/swarmai \
    && chown -R swarm:swarm /home/swarm/.swarm-ai /opt/swarmai

# Bind-mounted, not COPY'd: the package does not stay in a layer of its own.
RUN --mount=type=bind,source=swarmai-hive.tar.gz,target=/tmp/hive.tar.gz \
    tar xzf /tmp/hive.tar.gz --strip-components=1 -C /opt/swarmai \
    && bash /opt/swarmai/hive/ensure-node.sh \
    && chown -R swarm:swarm /opt/swarmai

# The host side of Feature Map replay, as an EC2 Hive's first boot and every update run it:
# hive/replay-host.sh taken from the package itself (as the update takes it, never from a tree
# the Hive user owns), run as root (no USER yet) in its image mode: the pinned Chromium's
# system libraries and fonts, the swarm-replay user, the shared location for the runtime and
# the task tools (outside the Hive user's home, which is 0700 with no way in for that user),
# and the sudoers rule for the Hive user.
# Its firewall rule (no metadata service for that user) needs root and nftables at run time,
# which this image does not have: the container runs as the Hive user, with no systemd. So
# replay here finds the unit that loads that rule inactive and reports its walks not_run with
# the reason (core/feature_map/replay_user.py network_rule_loaded), whatever way the platform
# hands out credentials, never running without it. A failed step fails the build.
RUN --mount=type=bind,source=swarmai-hive.tar.gz,target=/tmp/hive.tar.gz \
    mkdir -p -m 0755 /usr/local/libexec/swarmai \
    && (umask 077; tar xzf /tmp/hive.tar.gz --wildcards --no-wildcards-match-slash -O '*/hive/replay-host.sh' > /usr/local/libexec/swarmai/replay-host.sh) \
    && test -s /usr/local/libexec/swarmai/replay-host.sh \
    && chmod 0700 /usr/local/libexec/swarmai/replay-host.sh \
    && SWARM_USER=swarm bash /usr/local/libexec/swarmai/replay-host.sh image \
    && dnf clean all && rm -rf /var/cache/dnf

COPY --chown=swarm:swarm supervisor.py /opt/swarmai/hive/agentcore/supervisor.py

USER swarm
ENV HOME=/home/swarm
WORKDIR /opt/swarmai/backend

# Step 4 of user_data.py, and its code-intel grammars (a failure only warns, as there).
RUN python3.12 -m venv .venv \
    && .venv/bin/pip install -q --upgrade pip \
    && .venv/bin/pip install -q -e . \
    && (.venv/bin/python scripts/bundle_tree_sitter_grammars.py generated/tree_sitter_grammars --deadline 120 \
        || echo "WARN: tree-sitter grammars not bundled; code intel uses its regex path") \
    && rm -rf /home/swarm/.cache/pip

# 8080: the AgentCore contract (supervisor). 8081: the Hive backend, reached through the
# session endpoint (the runtime's allowedPorts).
EXPOSE 8080 8081

CMD ["python3.12", "/opt/swarmai/hive/agentcore/supervisor.py"]
