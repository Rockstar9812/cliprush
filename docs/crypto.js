// ClipRush bundle decryption: PBKDF2-SHA256 -> AES-256-GCM -> gunzip, browser built-ins only.
const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

export async function deriveKey(password, salt, iterations) {
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations },
    base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
}

export async function decryptBundle(env, password, cachedKey) {
  const key = cachedKey || (await deriveKey(password, b64(env.salt), env.iter || 200000));
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64(env.iv) }, key, b64(env.ct));
  const stream = new Blob([plain]).stream().pipeThrough(new DecompressionStream("gzip"));
  const text = await new Response(stream).text();
  return JSON.parse(text);
}
