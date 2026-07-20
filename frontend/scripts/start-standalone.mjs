import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const projectDir = process.cwd();
const standaloneDir = path.join(projectDir, ".next", "standalone");
const port = process.argv[2] ?? "3000";

fs.cpSync(path.join(projectDir, ".next", "static"), path.join(standaloneDir, ".next", "static"), {
  recursive: true,
});
fs.cpSync(path.join(projectDir, "public"), path.join(standaloneDir, "public"), {
  recursive: true,
});

process.env.HOSTNAME = "127.0.0.1";
process.env.PORT = port;
process.chdir(standaloneDir);
await import(pathToFileURL(path.join(standaloneDir, "server.js")).href);
