"use strict";

const path = require("path");
const { promises: fs } = require("fs");
const configuration = require("./configuration");

module.exports = {
    createDeferred: () => {
        let reject;
        let resolve;
        const promise = new Promise((a, b) => {
            resolve = a;
            reject = b;
        });

        return {
            reject: reject,
            resolve: resolve,
            promise: promise
        };
    },
    createTmpDir: () => {
        return fs.mkdtemp(path.join(configuration.tempdir, "mcs-"));
    },
    parseExifDate: (string) => {
        let timezone = false;

        const parts = string.match(/([0-9]{4}):([0-9]{2}):([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]+)?([\+|\-|Z].*)?/);

        if (parts[7] === "Z") {
            timezone = "+00:00";
            string = string.substr(0, string.length - 1); // Remove trailing Z
        } else if (parts[7]) {
            timezone = parts[7];
        }

        return {
            year: parts[1],
            month: parts[2],
            day: parts[3],
            hour: parts[4],
            minute: parts[5],
            second: parts[6],
            timezone: timezone
        };
    }
};
