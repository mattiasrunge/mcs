FROM node:10-stretch

ENV DLIB_VERSION v19.14

# Create app directory
WORKDIR /usr/src/app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    cmake \
    libimage-exiftool-perl \
    ffmpeg \
    libavformat-dev \
    libopenblas-dev \
    ufraw-batch \
    unoconv \
    && rm -rf /var/lib/apt/lists/*

# Build dlib
RUN git clone --branch $DLIB_VERSION --depth 1 https://github.com/davisking/dlib.git \
    && cd dlib \
    && mkdir build \
    && cd build \
    && cmake .. -DDLIB_NO_GUI_SUPPORT=1 -DBUILD_SHARED_LIBS=1 \
    && cmake --build .

# Install app dependencies
COPY package*.json ./
RUN DLIB_INCLUDE_DIR=/usr/src/app/dlib \
    DLIB_LIB_DIR=/usr/src/app/dlib/build/dlib \
    npm install

# Bundle app source
COPY . .

EXPOSE 8181
CMD [ "node", "index.js" ]
