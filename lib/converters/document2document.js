"use strict";

const path = require("path");
const co = require("bluebird").coroutine;
const tools = require("../tools");
const file = require("../file");

module.exports = co(function*(source, destination) {
    if (file.isPdf(source.mimetype)) {
        return source.filename;
    }

    let filename = path.join(destination.path, "document2document.pdf");
    let args = [];

    args.push("-f", "pdf");
    args.push("-o", filename);
    args.push(source.filename);

    yield tools.execute("unoconv", args, { cwd: destination.path });

    return filename;
});
