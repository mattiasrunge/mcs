"use strict";

const os = require("os");
const fs = require("fs/promises");
const tools = require("./tools");
const utils = require("./utils");
const file = require("./file");
const i2i = require("./converters/image2image");
const a2a = require("./converters/audio2audio");
const a2i = require("./converters/audio2image");
const v2i = require("./converters/video2image");
const v2v = require("./converters/video2video");
const d2d = require("./converters/document2document");
const d2i = require("./converters/document2image");

class Process {
    constructor() {
        this.threads = os.cpus().length;
        this.jobs = {};
    }

    async init(/* config */) {
    }

    async create(filename, format) {
        if (!this.jobs[format.filepath]) {
            format.threads = this.threads;

            const job = {
                filename: filename,
                format: format,
                deferred: utils.createDeferred(),
                running: false
            };

            this.jobs[format.filepath] = job;
            this.runJobs();
        }

        return await this.jobs[format.filepath].deferred.promise;
    }

    runJobs() {
        const imageJobsNotRunning = [];
        const imageJobsRunning = [];

        const videoAudioJobsNotRunning = [];
        const videoAudioJobsRunning = [];

        const jobsToStart = [];

        for (const [ , job ] of Object.entries(this.jobs)) {
            if (job.format.type === "image" || job.format.type === "document") {
                if (job.running) {
                    imageJobsRunning.push(job);
                } else {
                    imageJobsNotRunning.push(job);
                }
            } else if (job.format.type === "video" || job.format.type === "audio") {
                if (job.running) {
                    videoAudioJobsRunning.push(job);
                } else {
                    videoAudioJobsNotRunning.push(job);
                }
            }
        }

        for (let n = 0; n < Math.min(this.threads - imageJobsRunning.length, imageJobsNotRunning.length); n++) {
            jobsToStart.push(imageJobsNotRunning[n]);
        }

        for (let n = 0; n < Math.min(1 - videoAudioJobsRunning.length, videoAudioJobsNotRunning.length); n++) {
            jobsToStart.push(videoAudioJobsNotRunning[n]);
        }

        for (const job of jobsToStart) {
            job.running = true;

            this.start(job.filename, job.format)
            .then((filename) => {
                delete this.jobs[job.format.filepath];
                job.deferred.resolve(filename);
                this.runJobs();
            })
            .catch((error) => {
                console.error(error);
                delete this.jobs[job.format.filepath];
                job.deferred.reject(error);
                this.runJobs();
            });
        }
    }

    async start(filename, destination) {
        const source = { filename };

        source.mimetype = await file.getMimetype(filename);

        if (file.isImage(source.mimetype)) {
            const size = await file.getSize(filename);

            source.width = size.width;
            source.height = size.height;

            if (destination.type === "image") {
                return this.transform(i2i, source, destination);
            }
        } else if (file.isVideo(source.mimetype)) {
            const size = await file.getSize(filename);
            const exif = await file.getExif(filename);

            source.width = size.width;
            source.height = size.height;

            if (destination.type === "video") {
                destination.deinterlace = exif.Compression === "dvsd";

                return this.transform(v2v, source, destination);
            } else if (destination.type === "image") {
                return this.transform(v2i, source, destination);
            }
        } else if (file.isAudio(source.mimetype)) {
            if (destination.type === "audio") {
                return this.transform(a2a, source, destination);
            } else if (destination.type === "image") {
                return this.transform(a2i, source, destination);
            }
        } else if (file.isDocument(source.mimetype)) {
            if (destination.type === "image") {
                return this.transform(d2i, source, destination);
            } else if (destination.type === "document") {
                return this.transform(d2d, source, destination);
            }
        }

        throw new Error(`Unknown conversion path from source type to destination type "${destination.type}", mimetype is "${source.mimetype} and filename is ${filename}`);
    }

    async transform(converter, source, destination) {
        const dirpath = await utils.createTmpDir();
        let filepath = false;

        destination.path = dirpath;

        try {
            filepath = await converter(source, destination);
            await fs.copyFile(filepath, destination.filepath);
        } catch (error) {
            throw error;
        } finally {
            await tools.execute("rm", [ dirpath ]);
        }

        return destination.filepath;
    }

    status() {
        return Object.entries(this.jobs).map(([ , job ]) => job);
    }
}

module.exports = new Process();
