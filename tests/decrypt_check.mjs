// Decrypts a ClipRush blob with the exact WebCrypto code path the web app uses.
import { readFileSync } from "node:fs";
import { decryptBundle } from "../docs/crypto.js";

const [, , file, password] = process.argv;
const env = JSON.parse(readFileSync(file, "utf8"));
const obj = await decryptBundle(env, password);
process.stdout.write(JSON.stringify(obj));
