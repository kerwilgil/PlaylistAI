const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync("templates/index.html", "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];

scripts.forEach((match, index) => {
  new vm.Script(match[1], { filename: `templates/index.html:inline-${index}.js` });
});

console.log(`Sintaxis JavaScript válida (${scripts.length} bloques).`);
