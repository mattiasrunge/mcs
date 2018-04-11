"use strict";

const path = require("path");
const util = require("util");
const cp = require("child_process");
const spawn = require("child_process").spawn;
const log = require("./log")(module);

const exec = util.promisify(cp.exec);

let params = {};

module.exports = {
    bin: {
        convert: {
            name: "convert",
            versionMatch: /Version: ImageMagick (.*?) /,
            versionArgs: [ "-version" ],
            version: false,
            extraArgs: []
        },
        avconv: {
            name: "avconv",
            versionMatch: /(?:avconv|ffmpeg version) (.*?)(?:\:| Copyright)/,
            versionArgs: [ "-version" ],
            version: false,
            extraArgs: []
        },
        exiftool: {
            name: "exiftool",
            versionMatch: /(.*?)\n/,
            versionArgs: [ "-ver" ],
            version: false,
            extraArgs: [ "-config", path.join(__dirname, "..", "conf", "exiftool.conf") ]
        },
        file: {
            name: "file",
            versionMatch: /file-(.*?)\n/,
            versionArgs: [ "-v" ],
            version: false,
            extraArgs: []
        },
        waveform: {
            cmd: "./bin/waveform",
            name: "waveform",
            versionMatch: /Waveform (.*?)\n/,
            versionArgs: [ "-v" ],
            version: false,
            extraArgs: []
        },
        unoconv: {
            name: "unoconv",
            versionMatch: /(?:unoconv) (.*?)\n/,
            versionArgs: [ "--version" ],
            version: false,
            extraArgs: []
        }
    },
    init: async (config) => {
        params = config;

        await module.exports.initTool("convert");
        await module.exports.initTool("avconv");
        await module.exports.initTool("exiftool");
        await module.exports.initTool("file");
        await module.exports.initTool("waveform");
        await module.exports.initTool("unoconv");
    },
    initTool: async (name) => {
        if (!module.exports.bin[name]) {
            throw new Error(`${name} is not a tool that is available`);
        }

        const tool = module.exports.bin[name];

        try {
            if (params[tool]) {
                tool.name = params[name];
            } else if (tool.cmd) {
                tool.name = path.resolve(__dirname, "..", tool.cmd);
            } else {
                const output = await exec(`which ${tool.name}`);
                tool.name = output.stdout.match(/(.*?)\n/)[1];
            }

            if (tool.versionArgs) {
                const version = await module.exports.execute(name, tool.versionArgs);
                tool.version = version.match(tool.versionMatch)[1];
            }
        } catch (e) {
            log.error(`Failed to initialize ${name}, is it installed?`);
            throw e;
        }

        log.info(`Found ${name} version ${tool.version} at ${tool.name}`);
    },
    execute: async (tool, args, options) => {
        if (!module.exports.bin[tool]) {
            throw new Error(`${tool} is not a tool that is available`);
        }

        options = options || {};

        const params = [].concat(module.exports.bin[tool].extraArgs, args);
        const child = spawn(module.exports.bin[tool].name, params, options);

        log.debug("Command:", tool, args.join(" "));

        return new Promise((resolve, reject) => {
            let stdout = "";
            let stderr = "";

            child.stdout.on("data", (data) => stdout += data);
            child.stderr.on("data", (data) => stderr += data);

            child.on("close", (code) => {
                if (code !== 0) {
                    log.error("Command failed:", tool, args.join(" "));

                    return reject(stderr);
                }

                if (options.json) {
                    stdout = JSON.parse(stdout);
                }

                resolve(stdout);
            });
        });
    }
};
