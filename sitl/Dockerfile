# ArduPilot SITL — builds ArduCopter from source at a pinned tag.
# First build: ~15 minutes and ~2 GB. Cached afterward; only rebuilds on
# ARDUPILOT_TAG bump.

FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG ARDUPILOT_TAG=Copter-4.5.7

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates sudo lsb-release tzdata \
        python3 python3-pip python3-setuptools python3-dev python3-wheel \
        python3-lxml python3-numpy python3-matplotlib python3-scipy \
        python3-opencv python3-serial python3-pygame \
        build-essential ccache g++ gawk make wget unzip \
        libtool libtool-bin automake autoconf \
        libxml2-dev libxslt1-dev \
        libssl-dev libffi-dev \
        procps rsync openssh-client iproute2 net-tools \
    && rm -rf /var/lib/apt/lists/*

# Build user (ArduPilot build scripts don't love running as root)
RUN useradd -m -s /bin/bash ardu && echo "ardu ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers
USER ardu
WORKDIR /home/ardu

# Clone at a pinned tag (fast history is enough)
RUN git clone --recursive --depth 1 --branch ${ARDUPILOT_TAG} \
        https://github.com/ArduPilot/ardupilot.git

WORKDIR /home/ardu/ardupilot

# Install ArduPilot's own build prereqs (uses sudo internally).
# USER=ardu is required by the script for pip --user installs.
ENV USER=ardu
RUN Tools/environment_install/install-prereqs-ubuntu.sh -y

# Path for pymavlink/mavproxy pip --user installs
ENV PATH=/home/ardu/.local/bin:/home/ardu/ardupilot/Tools/autotest:$PATH

# Configure and build SITL Copter
RUN ./waf configure --board sitl \
 && ./waf copter

# Startup script and default params
COPY --chown=ardu:ardu start-sitl.sh /home/ardu/start-sitl.sh
COPY --chown=ardu:ardu defaults.parm /home/ardu/defaults.parm
RUN chmod +x /home/ardu/start-sitl.sh

# MAVLink UDP output goes to the bridge on this port
EXPOSE 14550/udp

CMD ["/home/ardu/start-sitl.sh"]
