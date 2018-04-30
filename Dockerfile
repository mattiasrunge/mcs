FROM node:10-stretch

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

# Install app dependencies
COPY package*.json ./
RUN npm install

# Bundle app source
COPY . .

EXPOSE 8181
CMD [ "node", "index.js" ]
