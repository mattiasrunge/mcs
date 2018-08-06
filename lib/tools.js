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
        ffmpeg: {
            name: "ffmpeg",
            versionMatch: /(?:ffmpeg version) (.*?)(?:\:| Copyright)/,
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
        mozjpeg: {
            name: "mozjpeg",
            versionMatch: false,
            version: false,
            extraArgs: []
        },
        file: {
            name: "file",
            versionMatch: /file-(.*?)\n/,
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
        },
        detectfaces: {
            cmd: "./lib/tools/detectfaces.js",
            name: "detectfaces",
            version: false,
            extraArgs: [],
            noparallel: true
        },
        rm: {
            name: "rm",
            versionMatch: /rm \(GNU coreutils\) (.*?)\n/,
            versionArgs: [ "--version" ],
            version: false,
            extraArgs: [ "-rf" ]
        }
    },
    init: async (config) => {
        params = config;

        for (const name of Object.keys(module.exports.bin)) {
            await module.exports.initTool(name);
        }
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
                try {
                    const version = await module.exports.execute(name, tool.versionArgs);
                    tool.version = version.match(tool.versionMatch)[1];
                } catch (error) {
                    tool.version = "unknown";
                }
            }

            if (tool.noparallel) {
                tool.currentRun = Promise.resolve();
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

        if (module.exports.bin[tool].noparallel) {
            try {
                await module.exports.bin[tool].currentRun;
            } catch (error) {}
        }

        options = options || {};

        const params = [].concat(module.exports.bin[tool].extraArgs, args);
        const child = spawn(module.exports.bin[tool].name, params, options);

        log.debug("Command:", tool, args.join(" "));

        return module.exports.bin[tool].currentRun = new Promise((resolve, reject) => {
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
