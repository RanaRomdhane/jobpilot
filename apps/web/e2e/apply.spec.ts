import { expect, test } from "@playwright/test";

import { sentCount, signIn } from "./helpers";

test("find a job, review the application, approve and send", async ({ page }) => {
  const before = await sentCount();
  await signIn(page);

  // search: the seeded jobs are listed with their match score
  await page.goto("/jobs");
  const job = page.getByRole("button", {
    name: /Voir le détail de l'offre Ingénieur Machine Learning F\/H/,
  });
  await expect(job).toBeVisible();
  await job.click();

  // the job already has its tailored CV and letter: go to the review
  await page.getByRole("link", { name: "Vérifier et envoyer" }).click();
  await expect(page).toHaveURL(/\/review\?application=\d+/);
  await expect(page.getByRole("heading", { name: "Ingénieur Machine Learning F/H" })).toBeVisible();
  await expect(page.getByText("Prêt à envoyer")).toBeVisible();
  await expect(page.getByLabel("À", { exact: true })).toHaveValue("jobs@exemplia-gulf.example.com");
  await expect(page.getByText("CV_Ada_Example_Exemplia_Gulf.pdf")).toBeVisible();

  // nothing is sent before the confirmation
  await page.getByRole("button", { name: "Approuver et envoyer" }).click();
  expect(await sentCount()).toBe(before);
  await page.getByRole("button", { name: "Envoyer maintenant" }).click();

  // the sender service consumed the approval and "Gmail" received exactly one message
  await expect(page.getByText("Envoyé à jobs@exemplia-gulf.example.com")).toBeVisible({
    timeout: 120_000,
  });
  expect(await sentCount()).toBe(before + 1);

  // tracked: the application is now in the pipeline as sent
  await page.goto("/applications");
  await expect(page.getByText("Ingénieur Machine Learning F/H").first()).toBeVisible();
});
