// This module has no Playwright dependency so its matching behavior can be tested separately.
const eventKinds = new Set(['response', 'requestfailed', 'console', 'pageerror']);

function validRule(rule) {
  if (!rule || !eventKinds.has(rule.kind) || !rule.reason?.trim()) throw new Error('Invalid phase rule');
  if (['response', 'requestfailed'].includes(rule.kind)) {
    if (!rule.method?.trim() || !rule.urlPattern?.trim()) throw new Error('Network rule needs method and urlPattern');
    new RegExp(rule.urlPattern);
  } else {
    if (!rule.messagePattern?.trim()) throw new Error('Message rule needs messagePattern');
    new RegExp(rule.messagePattern);
  }
  if (rule.kind === 'response' && !Number.isInteger(rule.status)) throw new Error('Response rule needs exact status');
  if (rule.kind === 'requestfailed' && !rule.errorText?.trim()) throw new Error('Failure rule needs exact errorText');
  if (!Number.isInteger(rule.min) || rule.min < 0) throw new Error('Rule min must be a nonnegative integer');
  if (rule.max !== undefined && (!Number.isInteger(rule.max) || rule.max < rule.min)) throw new Error('Rule max must be >= min');
}

function matches(rule, event) {
  if (rule.kind !== event.kind) return false;
  if (rule.kind === 'response' || rule.kind === 'requestfailed') {
    if (rule.method !== event.method || !new RegExp(rule.urlPattern).test(event.url)) return false;
    return rule.kind === 'response' ? rule.status === event.status : rule.errorText === event.errorText;
  }
  return new RegExp(rule.messagePattern).test(event.message);
}

export class PhaseRecorder {
  constructor() {
    this.active = null;
    this.phases = [];
    this.events = [];
    this.unexpected = [];
  }

  start(name, rules) {
    if (this.active || !name?.trim() || !Array.isArray(rules)) throw new Error('Invalid or nested phase');
    const entries = rules.map(rule => {
      validRule(rule);
      return { ...rule, count: 0 };
    });
    this.active = { name, rules: entries };
    this.phases.push(this.active);
  }

  record(event) {
    if (!eventKinds.has(event.kind)) throw new Error('Invalid browser event');
    const rules = this.active?.rules ?? [];
    const rule = rules.find(candidate => matches(candidate, event));
    // Successful responses are only relevant when explicitly required by a rule.
    if (event.kind === 'response' && event.status < 400 && !rule) return;
    const entry = { ...event, phase: this.active?.name ?? null, matched: rule?.reason ?? null };
    this.events.push(entry);
    if (rule) {
      rule.count += 1;
      if (rule.max !== undefined && rule.count > rule.max) this.unexpected.push(`${this.active.name}: ${rule.reason} exceeded max ${rule.max}`);
    } else {
      this.unexpected.push(`${entry.phase ?? 'outside phase'}: unexpected ${event.kind} ${event.method ?? ''} ${event.url ?? event.message ?? ''} ${event.status ?? event.errorText ?? ''}`.trim());
    }
  }

  end(name) {
    if (!this.active || this.active.name !== name) throw new Error('Phase end does not match active phase');
    for (const rule of this.active.rules) {
      if (rule.count < rule.min) this.unexpected.push(`${name}: ${rule.reason} observed ${rule.count}, required ${rule.min}`);
    }
    this.active = null;
  }

  finish() {
    if (this.active) {
      this.unexpected.push(`${this.active.name}: phase was not closed`);
      this.active = null;
    }
    return { version: 1, checked: true, phases: this.phases, events: this.events, unexpected: this.unexpected };
  }
}
