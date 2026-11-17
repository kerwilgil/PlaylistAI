const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync("templates/index.html", "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];

scripts.forEach((match, index) => {
  // Jinja renders this numeric value before the script reaches the browser.
  const script = match[1].replace(/{{\s*job_timeout_ms\s*}}/g, "390000");
  new vm.Script(script, { filename: `templates/index.html:inline-${index}.js` });
});

console.log(`Sintaxis JavaScript válida (${scripts.length} bloques).`);
