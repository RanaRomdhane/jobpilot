import { expect, type Page } from "@playwright/test";

export const STUB = process.env.E2E_STUB_URL ?? "http://127.0.0.1:8900";
export const PASSWORD = process.env.E2E_PASSWORD ?? "";
export const CV_FILE = process.env.E2E_CV ?? "";

/** Sign in through the real form; every journey starts here. */
export async function signIn(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Mot de passe").fill(PASSWORD);
  await page.getByRole("button", { name: "Se connecter" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page).not.toHaveURL(/login/);
}

/** Emails the sender service handed to the Gmail stub. */
export async function sentCount(): Promise<number> {
  const res = await fetch(`${STUB}/_sent`);
  return ((await res.json()) as { count: number }).count;
}
