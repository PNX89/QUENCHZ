/**
 * Proves the Python server's claims from outside the Python tree.
 *
 * This shares no code with the thing it is testing. It is the official MCP TypeScript SDK
 * talking to a running Python process over HTTP, which is the only honest way to support a
 * claim about interoperability: the Python suite and the Python server could agree with each
 * other about something that is not true of the protocol.
 *
 * Three claims are checked.
 *
 *   AUDIENCE  a correctly signed, unexpired token minted by the trusted issuer FOR A DIFFERENT
 *             RESOURCE reaches nothing. So does one minted for this resource AND another, and
 *             one minted with no audience at all.
 *   REACH     a tool this caller may not use is absent from its listing, and asking for it
 *             anyway returns bytes identical to asking for a name that never existed.
 *   BUDGET    one caller's burst cannot touch another caller's reserve. The expected numbers
 *             are DERIVED here from the budget parameters rather than read from the server,
 *             so agreeing with it is evidence rather than obedience.
 *
 * Source: ECB statistics.
 */

import { readFileSync } from 'node:fs';
import { Client } from '@modelcontextprotocol/sdk/client';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

interface Manifest {
  url: string;
  callers: { greedy: string; quiet: string };
  budget: { capacity: number; reserveFraction: number; callerCount: number; clock: string };
  tokens: Record<string, string>;
}

const NO_SUCH_TOOL = 'no tool by that name is available to this caller';

/**
 * How many checks this file makes, declared rather than discovered at the end of a run.
 *
 * The README states this number in prose and nothing could see it drift. `checks` is counted
 * as the run goes, so deleting one made the proof print "15 of 15 checks passed" and exit 0,
 * which is a smaller proof reporting success. Declared here, compared against the count below,
 * and read out of this file by the Python suite so the page and the proof cannot disagree.
 */
const DECLARED_CHECKS = 16;

let failures = 0;
let checks = 0;

function check(passed: boolean, description: string, detail = ''): void {
  checks += 1;
  if (passed) {
    console.log(`  ok    ${description}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${description}${detail ? `\n        ${detail}` : ''}`);
  }
}

async function connect(url: string, token: string): Promise<Client> {
  const client = new Client({ name: 'quenchz-interop-proof', version: '0.1.0' });
  const transport = new StreamableHTTPClientTransport(new URL(url), {
    requestInit: { headers: { Authorization: `Bearer ${token}` } },
  });
  await client.connect(transport);
  return client;
}

/**
 * Ask the server to open a session and report the HTTP status it answered with.
 *
 * This deliberately does NOT go through the SDK client, and the reason is a defect this proof
 * had until it was probed. The first version asked "did connect() throw?" and treated any
 * throw as the token having been refused. Removing the audience guard on the server made the
 * verifier raise instead of refusing, the server answered 500, connect() threw, and the proof
 * reported that the token "reached nothing" and passed. A server that answered 500 to
 * everything would have satisfied every audience check in this file.
 *
 * So the status is read directly and asserted. A refusal is 401 with a WWW-Authenticate
 * header. Anything else, including a 500, is a failure of the claim.
 */
async function attempt(url: string, token: string): Promise<{ status: number; challenge: string | null }> {
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      Accept: 'application/json, text/event-stream',
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: 1,
      method: 'initialize',
      params: {
        protocolVersion: '2025-06-18',
        capabilities: {},
        clientInfo: { name: 'quenchz-interop-proof', version: '0.1.0' },
      },
    }),
  });
  return { status: response.status, challenge: response.headers.get('www-authenticate') };
}

function textOf(result: unknown): string {
  const content = (result as { content?: Array<{ text?: string }> }).content ?? [];
  return content[0]?.text ?? '';
}

function isError(result: unknown): boolean {
  return (result as { isError?: boolean }).isError === true;
}

async function proveAudience(m: Manifest): Promise<void> {
  console.log('\nAUDIENCE: a token is only good at the door it was minted for');

  const admitted = await attempt(m.url, m.tokens.greedyForThisResource!);
  check(
    admitted.status === 200,
    'a token minted for this resource is admitted',
    `expected 200, got ${admitted.status}`,
  );

  for (const [name, description] of [
    ['forAnotherResource', 'a valid token minted for another resource is REFUSED, not merely failed'],
    ['forTwoResources', 'a token minted for this resource AND another is REFUSED'],
    ['withNoAudience', 'a token minted with no audience at all is REFUSED'],
  ] as const) {
    const refused = await attempt(m.url, m.tokens[name]!);
    check(
      refused.status === 401,
      description,
      `expected 401, got ${refused.status}${refused.status >= 500 ? ' (the server errored; that is not a refusal)' : ''}`,
    );
    check(
      refused.challenge !== null && refused.challenge.startsWith('Bearer '),
      `  and it answers with a Bearer challenge rather than an accident`,
      `www-authenticate was ${JSON.stringify(refused.challenge)}`,
    );
  }
}

/**
 * How many tool calls the reach proof spent on the greedy caller's budget.
 *
 * It is tracked rather than assumed because refused calls are charged. That is the whole
 * point of the budget being taken at the boundary: a refusal is never cheaper than an answer,
 * so probing for tools that do not exist is not free. The budget proof below subtracts this
 * and asserts the total, which is a stronger statement than either half alone.
 */
let refusedCallsAlreadyCharged = 0;

async function proveReach(m: Manifest): Promise<void> {
  console.log('\nREACH: a caller cannot learn what it may not use');

  const client = await connect(m.url, m.tokens.greedyForThisResource!);
  try {
    const listed = (await client.listTools()).tools.map((t) => t.name).sort();
    check(
      !listed.includes('series.catalogue'),
      'a tool this caller lacks the scope for is absent from its listing',
      `listed: ${listed.join(', ')}`,
    );

    const ungranted = await client.callTool({ name: 'series.catalogue', arguments: {} });
    const invented = await client.callTool({ name: 'series.cataloguz', arguments: {} });
    refusedCallsAlreadyCharged += 2;

    check(isError(ungranted) && isError(invented), 'both refusals are errors');
    check(
      Buffer.from(textOf(ungranted)).equals(Buffer.from(textOf(invented))),
      'an ungranted tool and a nonexistent one refuse with identical bytes',
      `ungranted=${JSON.stringify(textOf(ungranted))} invented=${JSON.stringify(textOf(invented))}`,
    );
    check(textOf(ungranted) === NO_SUCH_TOOL, 'and the refusal is the one this repository declares');
    check(
      !textOf(ungranted).includes('series') && !textOf(ungranted).includes('Unknown tool'),
      'the refusal neither echoes the name nor leaks the SDK wording',
    );
  } finally {
    await client.close();
  }
}

/** Call one cheap tool until the budget stops us, and report how many got through. */
async function drain(url: string, token: string, cap: number): Promise<number> {
  const client = await connect(url, token);
  let admitted = 0;
  try {
    for (let i = 0; i < cap; i += 1) {
      const result = await client.callTool({
        name: 'calendar.why',
        arguments: { day: '2026-04-03' },
      });
      if (isError(result)) return admitted;
      admitted += 1;
    }
  } finally {
    await client.close();
  }
  throw new Error(`${cap} calls and the budget never refused: this caller's calls are free`);
}

async function proveBudget(m: Manifest): Promise<void> {
  console.log('\nBUDGET: one burst cannot touch another caller\'s reserve');

  // Derived here, not read from the server. With a capacity of 60, half reserved and two
  // callers, each reserve is 15 and the common spare is 30.
  const reserve = (m.budget.capacity * m.budget.reserveFraction) / m.budget.callerCount;
  const spare = m.budget.capacity * (1 - m.budget.reserveFraction);
  const expectedGreedy = reserve + spare;

  check(m.budget.clock === 'frozen', 'the server clock is frozen, so the arithmetic is exact');

  const greedy = await drain(m.url, m.tokens.greedyForThisResource!, 500);
  const remaining = expectedGreedy - refusedCallsAlreadyCharged;
  check(
    greedy === remaining,
    `the bursting caller took what was left of its reserve and the spare: ${remaining}`,
    `expected ${expectedGreedy} minus ${refusedCallsAlreadyCharged} already charged, got ${greedy}`,
  );
  check(
    refusedCallsAlreadyCharged > 0 && greedy < expectedGreedy,
    `refused calls are charged too: the ${refusedCallsAlreadyCharged} refusals above cost budget`,
    `a refusal that cost nothing would leave the full ${expectedGreedy} here, and ${greedy} came back`,
  );

  const quiet = await drain(m.url, m.tokens.quietForThisResource!, 500);
  check(
    quiet === reserve,
    `the quiet caller still got exactly its reserve after that burst: ${reserve}`,
    `expected ${reserve}, got ${quiet}`,
  );
}

async function main(): Promise<number> {
  const manifestPath = process.argv[2];
  if (manifestPath === undefined) {
    console.error('usage: node dist/prove.js <manifest.json>');
    return 2;
  }
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as Manifest;

  console.log(`Proving the Python server from TypeScript, against ${manifest.url}`);
  console.log(`Using @modelcontextprotocol/sdk, which shares no code with the server.`);

  await proveAudience(manifest);
  await proveReach(manifest);
  await proveBudget(manifest);

  console.log(`\n${checks - failures} of ${checks} checks passed.`);
  if (failures > 0) console.log(`${failures} FAILED.`);
  console.log('Source: ECB statistics.');
  if (checks !== DECLARED_CHECKS) {
    console.log(
      `${checks} checks ran where ${DECLARED_CHECKS} are declared. The README states that ` +
        `number, so a proof that has quietly grown or shrunk is a page that is now wrong.`,
    );
    return 1;
  }
  return failures === 0 ? 0 : 1;
}

main().then(
  (code) => process.exit(code),
  (error) => {
    console.error('the proof could not be run:', error);
    process.exit(2);
  },
);
