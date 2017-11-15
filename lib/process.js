"use strict";

const os = require("os");
const co = require("bluebird").coroutine;
const fs = require("fs-extra-promise");
const utils = require("./utils");
const file = require("./file");
const i2i = require("./converters/image2image");
const a2a = require("./converters/audio2audio");
const a2i = require("./converters/audio2image");
const v2i = require("./converters/video2image");
const v2v = require("./converters/video2video");
const d2d = require("./converters/document2document");
const d2i = require("./converters/document2image");

let params = {};
let threads = os.cpus().length;
let jobs = {};

module.exports = {
    init: co(function*(config) {
        params = config;
    }),
    create: co(function*(filename, format) {
        if (!jobs[format.filepath]) {
            format.threads = threads;

            let job = {
                filename: filename,
                format: format,
                deferred: utils.createDeferred(),
                running: false
            };

            jobs[format.filepath] = job;
            module.exports.runJobs();
        }

        return yield jobs[format.filepath].deferred.promise;
    }),
    runJobs: () => {
        let imageJobsNotRunning = [];
        let imageJobsRunning = [];

        let videoAudioJobsNotRunning = [];
        let videoAudioJobsRunning = [];

        let jobsToStart = [];

        for (let key of Object.keys(jobs)) {
            let job = jobs[key];

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

        for (let job of jobsToStart) {
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
    start: co(function*(filename, destination) {
        let source = {
            filename: filename
        };

        source.mimetype = yield file.getMimetype(filename);

        if (file.isImage(source.mimetype)) {
            let size = yield file.getSize(filename);

            source.width = size.width;
            source.height = size.height;

            if (destination.type === "image") {
                return module.exports.transform(i2i, source, destination);
            }
        } else if (file.isVideo(source.mimetype)) {
            let size = yield file.getSize(filename);
            let exif = yield file.getExif(filename);

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

        throw new Error("Unknown conversion path from source type to destination type \"" + destination.type + "\", mimetype is \"" + source.mimetype + "\ and filename is " + filename);
    }),
    transform: co(function*(converter, source, destination) {
        let dirpath = yield utils.createTmpDir();
        let filepath = false;

        destination.path = dirpath;

        try {
            filepath = yield converter(source, destination);
        } catch (error) {
            yield fs.removeAsync(dirpath);
            throw error;
        }

        yield fs.copyAsync(filepath, destination.filepath);
        yield fs.removeAsync(dirpath);

        return destination.filepath;
    }),
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
