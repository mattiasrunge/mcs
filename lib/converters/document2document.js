"use strict";

const path = require("path");
const tools = require("../tools");
const file = require("../file");

module.exports = async (source, destination) => {
    if (file.isPdf(source.mimetype)) {
        return source.filename;
    }

    const filename = path.join(destination.path, "document2document.pdf");
    const args = [];

    args.push("-f", "pdf");
    args.push("-o", filename);
    args.push(source.filename);

    const tool = "unoconv";

    // if (file.isMsDocument(source.mimetype)) {
    //     tool = "doc2pdf";
    // }

    await tools.execute(tool, args, { cwd: destination.path });

    return filename;
};
