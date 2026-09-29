export type PhaseRule = {
  kind: 'response' | 'requestfailed' | 'console' | 'pageerror';
  reason: string;
  min: number;
  max?: number;
  method?: string;
  urlPattern?: string;
  status?: number;
  errorText?: string;
  messagePattern?: string;
};

export type BrowserEvent = {
  kind: PhaseRule['kind'];
  method?: string;
  url?: string;
  status?: number;
  errorText?: string;
  message?: string;
};

export class PhaseRecorder {
  active: unknown;
  unexpected: string[];
  start(name: string, rules: PhaseRule[]): void;
  record(event: BrowserEvent): void;
  end(name: string): void;
  finish(): {
    version: number;
    checked: boolean;
    phases: unknown[];
    events: BrowserEvent[];
    unexpected: string[];
  };
}
