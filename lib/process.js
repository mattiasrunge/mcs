"use strict";

const os = require("os");
const promisifyAll = require("bluebird").promisifyAll;
const co = require("bluebird").coroutine;
const tmp = promisifyAll(require("tmp"));
const fs = require("fs-extra-promise");
const utils = require("./utils");
const file = require("./file");
const a2a = require("./converters/audio2audio");
const i2i = require("./converters/image2image");
const v2i = require("./converters/video2image");
const v2v = require("./converters/video2video");

let params = {};
let cpuCount = 1;
let jobs = {};

module.exports = {
    init: co(function*(config) {
        params = config;

        cpuCount = os.cpus().length;
    }),
    create: co(function*(filename, format) {
        if (!jobs[filename]) {
            format.cpuCount = cpuCount;

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

            if (job.format.type === "image") {
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

        for (let n = 0; n < Math.min(imageJobsNotRunning.length - imageJobsRunning.length, cpuCount); n++) {
            jobsToStart.push(imageJobsNotRunning[n]);
        }

        for (let n = 0; n < Math.min(videoAudioJobsNotRunning.length - videoAudioJobsRunning.length, 1); n++) {
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

        let data = yield file.getSize(filename);

        source.mimetype = data.mimetype;
        source.width = data.width;
        source.height = data.height;

        if (file.isImage(source.mimetype)) {
            if (destination.type === "image") {
                return module.exports.transform(i2i, source, destination);
            }
        } else if (file.isVideo(source.mimetype)) {
            if (destination.type === "video") {
                return module.exports.transform(v2v, source, destination);
            } else if (destination.type === "image") {
                return module.exports.transform(v2i, source, destination);
            }
        } else if (file.isAudio(source.mimetype)) {
            if (destination.type === "audio") {
                return module.exports.transform(a2a, source, destination);
            }
        }

        throw new Error("Unknown conversion path from source type to destination path");
    }),
    transform: co(function*(converter, source, destination) {
        let dirpath = yield tmp.dirAsync();
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
    })
};
