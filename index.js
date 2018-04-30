"use strict";

const main = require("./lib/main");
const { version } = require("./package.json");

process
.on("SIGINT", () => { main.stop().then(process.exit); })
.on("SIGTERM", () => { main.stop().then(process.exit); });

main.start(process.env, version)
.catch((error) => {
    console.error("FATAL ERROR");
    console.error(error);
    console.error(error.stack);
    process.exit(255);
});
