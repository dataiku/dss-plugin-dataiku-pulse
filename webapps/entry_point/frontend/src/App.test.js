import { render, screen } from '@testing-library/react';
import App from './App';

test('renders welcome text', () => {
  render(<App />);
  const matches = screen.getAllByText(/high-level overview of platform activity/i);
  expect(matches.length).toBeGreaterThan(0);
});
