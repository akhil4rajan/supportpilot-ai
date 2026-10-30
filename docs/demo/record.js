// Records an end-to-end SupportPilot demo: hospital site and agent console side by side, with captions.
// Needs the app on :8000 with LLM_PROVIDER=demo, started on a fresh DATABASE_URL so the console queue is empty,
// and Playwright with Chromium. Usage: node docs/demo/record.js <outDir>  (writes a .webm; see docs/demo/README.md)
const path = require('path');
const { chromium } = require('playwright');

const outDir = process.argv[2];
const STAGE = 'file://' + path.join(__dirname, 'stage.html');
const TOTAL = 9;

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, recordVideo: { dir: outDir, size: { width: 1920, height: 1080 } } });
  const page = await ctx.newPage();
  await page.goto(STAGE);
  await page.evaluate(() => window.card(true, 'END-TO-END DEMO', 'SupportPilot AI',
    'A support assistant that answers from your own docs, shows customers only their own records, files tickets and hands off to a person.',
    'FastAPI · React · any LLM provider · github.com/akhil4rajan/supportpilot-ai'));
  await page.evaluate(() => document.fonts.ready);
  const site = page.frame({ name: 'site' });
  const con = page.frame({ name: 'console' });
  await site.waitForSelector('header select');
  await con.waitForSelector('form');
  const pause = (ms) => page.waitForTimeout(ms);
  // The panes are CSS-scaled iframes, where pointer hit-testing is unreliable: click via the DOM instead.
  const tap = (loc) => loc.evaluate((el) => el.click());
  const cap = (n, title, sub) => page.evaluate(([s, t, u]) => window.caption(s, t, u), [`${n}/${TOTAL}`, title, sub]);
  const focus = (w) => page.evaluate((x) => window.focusPane(x), w);

  const asUser = async (index) => {
    await site.locator('header select').selectOption({ index });
    await pause(900);
    const launcher = site.locator('button.launcher');
    await launcher.waitFor();
    await pause(400);
    await tap(launcher);
    await site.getByLabel('Message').waitFor();
    await pause(800);
  };
  const say = async (text, wait = 2600) => {
    const box = site.getByLabel('Message');
    await box.focus();
    await box.pressSequentially(text, { delay: 42 });
    await pause(300);
    await box.press('Enter');
    await pause(wait);
  };
  const agentSay = async (text, wait = 2200) => {
    const box = con.getByPlaceholder('Reply to the customer…');
    await box.focus();
    await box.pressSequentially(text, { delay: 38 });
    await pause(300);
    await tap(con.getByRole('button', { name: 'Send' }));
    await pause(wait);
  };

  await pause(4500);
  await page.evaluate(() => window.card(false));
  await pause(700);

  // 1. Agent signs in
  await focus('console');
  await cap(1, 'A human agent signs in to the console', 'Sam from patient services. Each company (tenant) has its own agents, queue and keys.');
  await pause(1200);
  await con.getByLabel('Company ID').fill('');
  await con.getByLabel('Company ID').pressSequentially('wattlebay', { delay: 60 });
  await con.getByLabel(/Agent ID/).fill('');
  await con.getByLabel(/Agent ID/).pressSequentially('sam', { delay: 60 });
  await con.getByLabel('Access key').fill('');
  await con.getByLabel('Access key').pressSequentially('wb-sam-demo-key', { delay: 40 });
  await pause(400);
  await tap(con.getByRole('button', { name: 'Sign in' }));
  await pause(2500);

  // 2. Guest asks a policy question
  await focus('site');
  await cap(2, 'A guest asks a policy question', 'The answer comes only from the hospital’s own documents, and cites the source file.');
  await asUser(0);
  await say('How much does parking cost?', 4500);

  // 3. Guest tries an appointment lookup
  await cap(3, 'Guests can’t look up appointments', 'Personal records need a signed-in user. There is no identity, so the lookup tool refuses.');
  await say('Where is appointment APT-1001?', 4000);

  // 4. Peter signs in
  await cap(4, 'Peter is signed in and asks about his appointment', 'His identity comes from a token signed by the hospital’s server. The AI never chooses whose records to read.');
  await asUser(1);
  await say('Where is appointment APT-1001?', 4500);

  // 5. Mary asks the same
  await cap(5, 'Mary asks about the same appointment number', '“Not found”: exactly the reply for a number that doesn’t exist, so nothing about Peter leaks.');
  await asUser(2);
  await say('Where is appointment APT-1001?', 5000);

  // 6. Mary files a ticket
  await cap(6, 'Mary reports a problem and a ticket is filed', 'Jira in production (an in-memory tracker here), with her contact details and the chat transcript attached.');
  await say('Please open a ticket: the car park pay station rejected my validation code', 5000);

  // 7. Peter asks for a person
  await cap(7, 'Peter asks for a person', 'A phrase like this skips the AI completely and hands off straight away. It appears in Sam’s queue in real time.');
  await asUser(1);
  await say('I want to talk to a person', 2500);
  await focus('console');
  await pause(1500);
  await tap(con.locator('.list button.item').first());
  await pause(2500);

  // 8. Sam takes over
  await cap(8, 'Sam takes over and they chat live', 'Sam sees why it was escalated. While a human owns the chat, the bot stays silent. Messages travel over WebSockets.');
  await tap(con.getByRole('button', { name: 'Take over' }));
  await pause(1500);
  await agentSay('Hi Peter, Sam from patient services here. How can I help with your appointment?', 2500);
  await focus('site');
  await say('Thanks Sam! Could I move it to Thursday morning?', 2000);
  await focus('console');
  await pause(1200);
  await agentSay('Done: you are now booked for Thursday 22/10/2026 at 9:00am. Anything else?', 3000);
  await focus('site');
  await pause(2500);

  // 9. Resolve
  await cap(9, 'Sam resolves the conversation', 'If Peter writes again, the bot picks it back up. Every lookup and ticket is in the audit log.');
  await focus('console');
  await pause(1200);
  await tap(con.getByRole('button', { name: 'Resolve' }));
  await pause(3500);

  await page.evaluate(() => window.card(true, 'WHAT YOU JUST SAW', 'Answers, privacy, tickets, people',
    'Grounded answers with sources · identity from a signed token, never from the AI · tickets with the transcript · a model-free path to a human · live agent takeover.',
    'github.com/akhil4rajan/supportpilot-ai · docs/pitch for both decks'));
  await pause(6000);

  const video = page.video();
  await ctx.close();
  console.log(await video.path());
  await browser.close();
})().catch(async (e) => { console.error(e); process.exit(1); });
