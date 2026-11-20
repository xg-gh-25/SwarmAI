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

# Step 1 of user_data.py, plus what the image lacks that an EC2 AMI has.
RUN dnf install -y python3.12 python3.12-pip python3.12-devel nodejs20 npm git gcc make \
        tar gzip shadow-utils procps-ng util-linux \
    && dnf clean all && rm -rf /var/cache/dnf

RUN useradd -m -s /bin/bash swarm \
    && mkdir -p /home/swarm/.swarm-ai/logs /opt/swarmai \
    && chown -R swarm:swarm /home/swarm/.swarm-ai /opt/swarmai

# Bind-mounted, not COPY'd: the package does not stay in a layer of its own.
RUN --mount=type=bind,source=swarmai-hive.tar.gz,target=/tmp/hive.tar.gz \
    tar xzf /tmp/hive.tar.gz --strip-components=1 -C /opt/swarmai \
    && bash /opt/swarmai/hive/ensure-node.sh \
    && chown -R swarm:swarm /opt/swarmai

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
