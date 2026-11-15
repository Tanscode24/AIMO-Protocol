// Shared, NON-secret constants for the isolated E2E stack (test-only values, never production secrets).
export const E2E = {
  apiPort: 8100,
  uiPort: 3100,
  mockValioPort: 8199,
  apiUrl: "http://localhost:8100",
  uiUrl: "http://localhost:3100",
  demoPassword: "e2e-demo-password-2026",
  agentKey: "e2e-agent-key-123456",
  mockValioKey: "e2e-mock-valio-key",
};
