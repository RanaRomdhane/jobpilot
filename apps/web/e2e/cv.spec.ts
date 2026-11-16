import { expect, test } from "@playwright/test";

import { CV_FILE, signIn } from "./helpers";

test("upload a CV and see its ATS report", async ({ page }) => {
  await signIn(page);
  await page.goto("/resume");
  await expect(page.getByText("Données de démonstration.")).toBeVisible();

  await page.locator('input[type="file"]').setInputFiles(CV_FILE);

  // the worker extracts the text, the (stub) model parses it, the ATS rules and review run
  await expect(page.getByText("Score ATS").first()).toBeVisible({ timeout: 180_000 });
  await expect(page.getByText("cv.pdf").first()).toBeVisible();
});
