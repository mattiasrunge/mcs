"use strict";

const co = require("bluebird").coroutine;
const promisify = require("bluebird").promisify;
const exec = promisify(require("child_process").exec);
const spawn = require("child_process").spawn;
const log = require("./log")(module);

let params = {};

module.exports = {
    bin: {
        convert: {
            name: "convert",
            versionMatch: /Version: ImageMagick (.*?) /,
            versionArgs: [ "-version" ],
            version: false
        },
        avconv: {
            name: "avconv",
            versionMatch: /(?:avconv|ffmpeg version) (.*?)(?:\:| Copyright)/,
            versionArgs: [ "-version" ],
            version: false
        },
        exiftool: {
            name: "exiftool",
            versionMatch: /(.*?)\n/,
            versionArgs: [ "-ver" ],
            version: false
        },
        file: {
            name: "file",
            versionMatch: /file-(.*?)\n/,
            versionArgs: [ "-v" ],
            version: false
        }
    },
    init: co(function*(config) {
        params = config;

        yield module.exports.initTool("convert");
        yield module.exports.initTool("avconv");
        yield module.exports.initTool("exiftool");
        yield module.exports.initTool("file");
    }),
    initTool: co(function*(name) {
        if (!module.exports.bin[name]) {
            throw new Error(name + " is not a tool that is available");
        }

        let tool = module.exports.bin[name];

        try {
            if (params[tool]) {
                tool.name = params[name];
            } else {
                tool.name = (yield exec("which " + tool.name)).match(/(.*?)\n/)[1];
            }

            let version = yield module.exports.execute(name, tool.versionArgs);
            tool.version = version.match(tool.versionMatch)[1];
        } catch (e) {
            log.error("Failed to initialize " + name + ", is it installed?");
            throw e;
        }

        log.info("Found " + name + " version " + tool.version + " at " + tool.name);
    }),
    execute: co(function*(tool, args, options) {
        if (!module.exports.bin[tool]) {
            throw new Error(tool + " is not a tool that is available");
        }

        options = options || {};

        let child = spawn(module.exports.bin[tool].name, args, options);

        return new Promise((resolve, reject) => {
            let stdout = "";
            let stderr = "";

            child.stdout.on("data", (data) => stdout += data);
            child.stderr.on("data", (data) => stderr += data);

            child.on("close", (code) => {
                if (code !== 0) {
                    return reject(stderr);
                }

                if (options.json) {
                    stdout = JSON.parse(stdout);
                }

                resolve(stdout);
            });
        });
    })
};
