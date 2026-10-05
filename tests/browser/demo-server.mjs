// Deliberately small local integration fixture, not an example production server.
import { createServer } from 'node:http';
import { readFile, writeFile } from 'node:fs/promises';

const html = `<!doctype html>
<html lang="en"><meta charset="utf-8"><title>Project Check isolated demo</title>
<link rel="icon" href="data:,">
<h1>Test profile</h1>
<form><label>Name <input name="name" required></label><button>Save</button></form>
<p role="status">Loading</p><button id="fault" type="button">Trigger fault</button>
<script>
const input = document.querySelector('input');
const status = document.querySelector('[role=status]');
fetch('/api/profile').then(r => r.json()).then(profile => {
  input.value = profile.name; status.textContent = 'Loaded from server';
});
document.querySelector('form').addEventListener('submit', async event => {
  event.preventDefault();
  const response = await fetch('/api/profile', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ name: input.value }),
  });
  const profile = await response.json();
  status.textContent = response.ok ? 'Saved: ' + profile.name : 'Save failed';
});
document.querySelector('#fault').addEventListener('click', async () => {
  const fault = new URLSearchParams(location.search).get('fault');
  if (fault === 'console') console.error('DEMO_CONSOLE_ERROR');
  if (fault === 'pageerror') setTimeout(() => { throw new Error('DEMO_PAGE_ERROR'); }, 0);
  if (fault === 'network') await fetch('/api/error');
  if (fault === 'requestfailed') await fetch('/api/disconnect').catch(() => {});
  status.textContent = 'Fault triggered';
});
</script></html>`;

export async function startDemo(dataFile) {
  await writeFile(dataFile, JSON.stringify({ name: 'Initial test user' }));
  const server = createServer(async (request, response) => {
    const url = new URL(request.url, 'http://localhost');
    const json = (status, data) => {
      response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
      response.end(JSON.stringify(data));
    };
    try {
      if (url.pathname === '/') {
        response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
        response.end(html);
      } else if (url.pathname === '/api/profile' && request.method === 'GET') {
        json(200, JSON.parse(await readFile(dataFile, 'utf8')));
      } else if (url.pathname === '/api/profile' && request.method === 'POST') {
        let body = '';
        for await (const chunk of request) body += chunk;
        const value = JSON.parse(body);
        if (typeof value.name !== 'string' || !value.name.trim()) return json(422, { error: 'Name required' });
        const profile = { name: value.name.trim() };
        await writeFile(dataFile, JSON.stringify(profile));
        json(200, profile);
      } else if (url.pathname === '/api/error') {
        json(500, { error: 'Intentional integration fault' });
      } else if (url.pathname === '/api/disconnect') {
        request.socket.destroy();
      } else {
        json(404, { error: 'Not found' });
      }
    } catch (error) {
      json(500, { error: String(error) });
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  return { url: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve())) };
}
