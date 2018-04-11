"use strict";

const os = require("os");
const fs = require("fs-extra");
const utils = require("./utils");
const file = require("./file");
const i2i = require("./converters/image2image");
const a2a = require("./converters/audio2audio");
const a2i = require("./converters/audio2image");
const v2i = require("./converters/video2image");
const v2v = require("./converters/video2video");
const d2d = require("./converters/document2document");
const d2i = require("./converters/document2image");

const threads = os.cpus().length;
const jobs = {};

module.exports = {
    init: async (/* config */) => {
    },
    create: async (filename, format) => {
        if (!jobs[format.filepath]) {
            format.threads = threads;

            const job = {
                filename: filename,
                format: format,
                deferred: utils.createDeferred(),
                running: false
            };

            jobs[format.filepath] = job;
            module.exports.runJobs();
        }

        return await jobs[format.filepath].deferred.promise;
    },
    runJobs: () => {
        const imageJobsNotRunning = [];
        const imageJobsRunning = [];

        const videoAudioJobsNotRunning = [];
        const videoAudioJobsRunning = [];

        const jobsToStart = [];

        for (const key of Object.keys(jobs)) {
            const job = jobs[key];

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

        for (let n = 0; n < Math.min(threads - imageJobsRunning.length, imageJobsNotRunning.length); n++) {
            jobsToStart.push(imageJobsNotRunning[n]);
        }

        for (let n = 0; n < Math.min(1 - videoAudioJobsRunning.length, videoAudioJobsNotRunning.length); n++) {
            jobsToStart.push(videoAudioJobsNotRunning[n]);
        }

        for (const job of jobsToStart) {
            job.running = true;

            module.exports.start(job.filename, job.format)
            .then((filename) => {
                delete jobs[job.format.filepath];
                job.deferred.resolve(filename);
                module.exports.runJobs();
            })
            .catch((error) => {
                console.error(error);
                delete jobs[job.format.filepath];
                job.deferred.reject(error);
                module.exports.runJobs();
            });
        }
    },
    start: async (filename, destination) => {
        const source = {
            filename: filename
        };

        source.mimetype = await file.getMimetype(filename);

        if (file.isImage(source.mimetype)) {
            const size = await file.getSize(filename);

            source.width = size.width;
            source.height = size.height;

            if (destination.type === "image") {
                return module.exports.transform(i2i, source, destination);
            }
        } else if (file.isVideo(source.mimetype)) {
            const size = await file.getSize(filename);
            const exif = await file.getExif(filename);

            source.width = size.width;
            source.height = size.height;

            if (destination.type === "video") {
                destination.deinterlace = exif.Compression === "dvsd";

                return module.exports.transform(v2v, source, destination);
            } else if (destination.type === "image") {
                return module.exports.transform(v2i, source, destination);
            }
        } else if (file.isAudio(source.mimetype)) {
            if (destination.type === "audio") {
                return module.exports.transform(a2a, source, destination);
            } else if (destination.type === "image") {
                return module.exports.transform(a2i, source, destination);
            }
        } else if (file.isDocument(source.mimetype)) {
            if (destination.type === "image") {
                return module.exports.transform(d2i, source, destination);
            } else if (destination.type === "document") {
                return module.exports.transform(d2d, source, destination);
            }
        }

        throw new Error(`Unknown conversion path from source type to destination type "${destination.type}", mimetype is "${source.mimetype} and filename is ${filename}`);
    },
    transform: async (converter, source, destination) => {
        const dirpath = await utils.createTmpDir();
        let filepath = false;

        destination.path = dirpath;

        try {
            filepath = await converter(source, destination);
        } catch (error) {
            await fs.remove(dirpath);
            throw error;
        }

        await fs.copy(filepath, destination.filepath);
        await fs.remove(dirpath);

        return destination.filepath;
    },
    status: () => {
        return Object.keys(jobs).map((id) => {
            return {
                filename: jobs[id].filename,
                format: jobs[id].format,
                running: jobs[id].running
            };
        });
    }
};
