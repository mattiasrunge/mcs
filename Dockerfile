FROM ubuntu:19.10

ENV NODEJS_VERSION 14

# Create app directory
WORKDIR /usr/src/app

RUN echo "deb http://old-releases.ubuntu.com/ubuntu eoan Release restricted" >> /etc/apt/sources.list \
 && echo "deb http://old-releases.ubuntu.com/ubuntu eoan backports restricted" >> /etc/apt/sources.list \
 && echo "deb http://old-releases.ubuntu.com/ubuntu eoan-updates main restricted" >> /etc/apt/sources.list \
 && echo "deb http://old-releases.ubuntu.com/ubuntu eoan-security main restricted" >> /etc/apt/sources.list

# Install system dependencies
RUN apt-get update \
    && apt-get install -y \
    software-properties-common \
    python3-pip \
    python3-dev \
    python3-numpy \
    wget \
#    nasm \
    cmake \
    dcraw \
    git \
    pkg-config \
#    libpng-dev \
#    dh-autoreconf \
    libimage-exiftool-perl \
    unoconv \
    ffmpeg \
#    libavformat-dev \
#    libopenblas-dev \
#    libx11-dev \
    imagemagick \
    nano \
    && apt-get clean \
    && rm -rf /tmp/* /var/tmp/*

# Install face recognition
RUN pip3 install wheel sklearn face_recognition

# Install nodejs
RUN wget -qO- https://dl.yarnpkg.com/debian/pubkey.gpg | apt-key add -
RUN echo "deb https://dl.yarnpkg.com/debian/ stable main" | tee /etc/apt/sources.list.d/yarn.list
RUN wget -qO- https://deb.nodesource.com/setup_${NODEJS_VERSION}.x | bash -
RUN apt-get install -y nodejs yarn && rm -rf /var/lib/apt/lists/*



# Install app dependencies
COPY ./package.json ./
RUN yarn

COPY conf/policy.xml /etc/ImageMagick-6/

# Bundle app source
COPY . .

EXPOSE 8181
CMD [ "node", "index.js" ]
