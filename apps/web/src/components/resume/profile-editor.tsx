"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, CircleAlert, Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useMemo, type ReactNode } from "react";
import {
  get,
  useFieldArray,
  useForm,
  type Control,
  type FieldErrors,
  type Path,
  type UseFormRegister,
} from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { IconButton } from "@/components/icon-button";
import { Section } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, unwrap } from "@/lib/api/client";
import {
  fromForm,
  toForm,
  warningIndex,
  type FormValues,
  type Profile,
  type Warning,
} from "@/lib/resume";
import { cn } from "@/lib/utils";

const text = z.string();
const req = z.string().trim().min(1, "required");
const date = z
  .string()
  .trim()
  .regex(/^(\d{4}(-(0[1-9]|1[0-2]))?)?$/, "invalidDate");
const bullets = z.array(z.object({ id: text, text }));

const schema = z.object({
  language: z.enum(["fr", "en", "other"]),
  contact: z.object({
    full_name: text,
    email: z.union([z.literal(""), z.email("invalidEmail")]),
    phone: text,
    location: text,
  }),
  headline: text,
  summary: text,
  experiences: z.array(
    z.object({
      id: text,
      company: req,
      title: req,
      location: text,
      start: date,
      end: date,
      current: z.boolean(),
      employment_type: text,
      bullets,
      technologies: text,
    }),
  ),
  education: z.array(
    z.object({
      id: text,
      institution: req,
      degree: text,
      field: text,
      location: text,
      start: date,
      end: date,
      grade: text,
      details: bullets,
    }),
  ),
  projects: z.array(
    z.object({
      id: text,
      name: req,
      role: text,
      url: text,
      start: date,
      end: date,
      description: text,
      bullets,
      technologies: text,
    }),
  ),
  skills: z.array(
    z.object({
      id: text,
      name: req,
      kind: z.enum(["hard", "soft", "tool", "other"]),
      skills: text,
    }),
  ),
  languages: z.array(z.object({ id: text, name: req, level: text, cefr: text })),
  certifications: z.array(
    z.object({ id: text, name: req, issuer: text, date, url: text, credential_id: text }),
  ),
  links: z.array(z.object({ id: text, kind: text, url: req, label: text })),
  extra_sections: z.array(z.object({ id: text, title: req, bullets })),
}) satisfies z.ZodType<FormValues>;

type Ctx = {
  register: UseFormRegister<FormValues>;
  errors: FieldErrors<FormValues>;
  warnings: Map<string, string[]>;
};

function Field({
  ctx,
  name,
  label,
  hint,
  warnKey,
  multiline,
  className,
  type,
}: {
  ctx: Ctx;
  name: Path<FormValues>;
  label: string;
  hint?: string;
  warnKey?: string;
  multiline?: boolean;
  className?: string;
  type?: string;
}) {
  const t = useTranslations("resume.profile");
  const id = `f-${name.replace(/\./g, "-")}`;
  const error = (get(ctx.errors, name) as { message?: string } | undefined)?.message;
  const warn = warnKey ? ctx.warnings.get(warnKey) : undefined;
  const describedBy = error ? `${id}-error` : warn ? `${id}-warn` : hint ? `${id}-hint` : undefined;
  const common = {
    id,
    "aria-invalid": error ? true : undefined,
    "aria-describedby": describedBy,
    className: cn(warn && !error && "border-signal/70"),
    ...ctx.register(name),
  };
  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={id}>{label}</Label>
      {multiline ? <Textarea rows={3} {...common} /> : <Input type={type} {...common} />}
      {error ? (
        <p id={`${id}-error`} className="text-xs text-destructive">
          {t(error)}
        </p>
      ) : warn ? (
        <p id={`${id}-warn`} className="flex items-start gap-1 text-xs text-signal">
          <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" />
          {t("unverified")}
          {name.endsWith("skills") ? ` (${warn.join(", ")})` : null}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-xs text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

function SelectField({
  ctx,
  name,
  label,
  options,
}: {
  ctx: Ctx;
  name: Path<FormValues>;
  label: string;
  options: { value: string; label: string }[];
}) {
  const id = `f-${name.replace(/\./g, "-")}`;
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <select
        id={id}
        {...ctx.register(name)}
        className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none dark:bg-input/30"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

type BulletListName =
  | `experiences.${number}.bullets`
  | `education.${number}.details`
  | `projects.${number}.bullets`
  | `extra_sections.${number}.bullets`;

function Bullets({
  ctx,
  control,
  name,
  label,
}: {
  ctx: Ctx;
  control: Control<FormValues>;
  name: BulletListName;
  label: string;
}) {
  const t = useTranslations("resume.profile");
  const { fields, append, remove } = useFieldArray({ control, name, keyName: "key" });
  return (
    <fieldset className="space-y-2 sm:col-span-2">
      <legend className="mb-1.5 text-sm font-medium">{label}</legend>
      {fields.map((f, i) => {
        const fieldName = `${name}.${i}.text` as Path<FormValues>;
        const id = `f-${fieldName.replace(/\./g, "-")}`;
        const warn = f.id ? ctx.warnings.get(`${f.id}:text`) : undefined;
        return (
          <div key={f.key} className="flex items-start gap-1.5">
            <div className="min-w-0 flex-1">
              <Label htmlFor={id} className="sr-only">
                {`${label} ${i + 1}`}
              </Label>
              <Textarea
                id={id}
                rows={2}
                className={cn("min-h-0", warn && "border-signal/70")}
                aria-describedby={warn ? `${id}-warn` : undefined}
                {...ctx.register(fieldName)}
              />
              {warn ? (
                <p id={`${id}-warn`} className="mt-1 flex items-center gap-1 text-xs text-signal">
                  <CircleAlert aria-hidden className="size-3.5" />
                  {t("unverified")}
                </p>
              ) : null}
            </div>
            <IconButton label={t("remove")} onClick={() => remove(i)}>
              <Trash2 aria-hidden />
            </IconButton>
          </div>
        );
      })}
      <Button type="button" variant="ghost" size="sm" onClick={() => append({ id: "", text: "" })}>
        <Plus aria-hidden />
        {t("fields.bullets")}
      </Button>
    </fieldset>
  );
}

function ListBlock({
  title,
  addLabel,
  count,
  onAdd,
  children,
}: {
  title: string;
  addLabel: string;
  count: number;
  onAdd: () => void;
  children: ReactNode;
}) {
  return (
    <Section
      title={title}
      aside={<span className="text-sm text-muted-foreground tabular-nums">{count}</span>}
    >
      <div className="space-y-3">{children}</div>
      <Button type="button" variant="outline" size="sm" className="mt-3" onClick={onAdd}>
        <Plus aria-hidden />
        {addLabel}
      </Button>
    </Section>
  );
}

function ItemCard({
  heading,
  index,
  total,
  onMove,
  onRemove,
  children,
}: {
  heading: string;
  index: number;
  total: number;
  onMove: (from: number, to: number) => void;
  onRemove: () => void;
  children: ReactNode;
}) {
  const t = useTranslations("resume.profile");
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <p className="min-w-0 truncate text-sm font-medium">{heading}</p>
        <div className="flex shrink-0 items-center">
          <IconButton
            label={t("moveUp")}
            disabled={index === 0}
            onClick={() => onMove(index, index - 1)}
          >
            <ArrowUp aria-hidden />
          </IconButton>
          <IconButton
            label={t("moveDown")}
            disabled={index === total - 1}
            onClick={() => onMove(index, index + 1)}
          >
            <ArrowDown aria-hidden />
          </IconButton>
          <IconButton label={t("remove")} onClick={onRemove}>
            <Trash2 aria-hidden />
          </IconButton>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">{children}</div>
    </div>
  );
}

export function ProfileEditor({
  initial,
  warnings = [],
  mode,
  sourceResumeId,
  onSaved,
}: {
  initial: Profile;
  warnings?: Warning[];
  mode: "draft" | "master";
  sourceResumeId?: number;
  onSaved?: () => void;
}) {
  const t = useTranslations("resume.profile");
  const queryClient = useQueryClient();
  // keyed on content, not identity: a background refetch must not wipe unsaved edits
  const initialKey = JSON.stringify(initial);
  const defaults = useMemo(() => toForm(JSON.parse(initialKey) as Profile), [initialKey]);
  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: defaults });
  const {
    control,
    register,
    reset,
    watch,
    handleSubmit,
    formState: { errors, isDirty },
  } = form;
  useEffect(() => reset(defaults), [defaults, reset]);
  const ctx: Ctx = {
    register,
    errors,
    warnings: useMemo(() => warningIndex(warnings), [warnings]),
  };

  const exp = useFieldArray({ control, name: "experiences", keyName: "key" });
  const edu = useFieldArray({ control, name: "education", keyName: "key" });
  const prj = useFieldArray({ control, name: "projects", keyName: "key" });
  const skl = useFieldArray({ control, name: "skills", keyName: "key" });
  const lng = useFieldArray({ control, name: "languages", keyName: "key" });
  const crt = useFieldArray({ control, name: "certifications", keyName: "key" });
  const lnk = useFieldArray({ control, name: "links", keyName: "key" });
  const ext = useFieldArray({ control, name: "extra_sections", keyName: "key" });

  const save = useMutation({
    mutationFn: (values: FormValues) =>
      unwrap(
        api.PUT("/api/v1/resume/master", {
          body: { profile: fromForm(values), source_resume_id: sourceResumeId ?? null },
        }),
      ),
    onSuccess: async (saved) => {
      toast.success(t("saved"));
      reset(toForm(saved.profile));
      await queryClient.invalidateQueries({ queryKey: ["resume"] });
      onSaved?.();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const f = (k: string) => t(`fields.${k}`);
  const kinds = (["hard", "soft", "tool", "other"] as const).map((k) => ({
    value: k,
    label: t(`kinds.${k}`),
  }));
  const linkKinds = (
    ["linkedin", "github", "gitlab", "portfolio", "website", "other"] as const
  ).map((k) => ({ value: k, label: t(`linkKinds.${k}`) }));
  const cefr = [
    { value: "", label: t("cefrNone") },
    ...["A1", "A2", "B1", "B2", "C1", "C2"].map((v) => ({ value: v, label: v })),
    { value: "native", label: t("cefrNative") },
  ];
  const heading = (main: string | undefined, sub: string | undefined, i: number) =>
    [main, sub].filter(Boolean).join(" · ") || t("item", { n: i + 1 });
  const dateHint = t("hints.date");

  return (
    <form
      noValidate
      onSubmit={handleSubmit(
        (v) => save.mutate(v),
        () => toast.error(t("fixErrors")),
      )}
      className="space-y-10"
    >
      <p
        className={cn(
          "rounded-lg border p-3 text-sm",
          mode === "draft" ? "border-signal/40 bg-signal/5" : "text-muted-foreground",
        )}
      >
        {mode === "draft" ? t("draftBanner") : t("masterBanner")}
        {warnings.length ? (
          <span className="mt-1 flex items-center gap-1 font-medium text-signal">
            <CircleAlert aria-hidden className="size-4" />
            {t("unverifiedCount", { count: warnings.length })}
          </span>
        ) : null}
      </p>

      <Section title={t("sections.contact")}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            ctx={ctx}
            name="contact.full_name"
            label={f("full_name")}
            warnKey="contact.full_name"
          />
          <Field ctx={ctx} name="contact.email" label={f("email")} type="email" />
          <Field ctx={ctx} name="contact.phone" label={f("phone")} type="tel" />
          <Field ctx={ctx} name="contact.location" label={f("location")} />
          <SelectField
            ctx={ctx}
            name="language"
            label={f("language")}
            options={(["fr", "en", "other"] as const).map((v) => ({
              value: v,
              label: t(`cvLanguages.${v}`),
            }))}
          />
        </div>
      </Section>

      <Section title={t("sections.headline")}>
        <div className="grid gap-3">
          <Field ctx={ctx} name="headline" label={f("headline")} warnKey="headline" />
          <Field ctx={ctx} name="summary" label={f("summary")} warnKey="summary" multiline />
        </div>
      </Section>

      <ListBlock
        title={t("sections.experiences")}
        addLabel={t("add.experiences")}
        count={exp.fields.length}
        onAdd={() =>
          exp.append({
            id: "",
            company: "",
            title: "",
            location: "",
            start: "",
            end: "",
            current: false,
            employment_type: "",
            bullets: [],
            technologies: "",
          })
        }
      >
        {exp.fields.map((item, i) => (
          <ItemCard
            key={item.key}
            heading={heading(watch(`experiences.${i}.title`), watch(`experiences.${i}.company`), i)}
            index={i}
            total={exp.fields.length}
            onMove={exp.move}
            onRemove={() => exp.remove(i)}
          >
            <Field
              ctx={ctx}
              name={`experiences.${i}.title`}
              label={f("title")}
              warnKey={`${item.id}:title`}
            />
            <Field
              ctx={ctx}
              name={`experiences.${i}.company`}
              label={f("company")}
              warnKey={`${item.id}:company`}
            />
            <Field ctx={ctx} name={`experiences.${i}.start`} label={f("start")} hint={dateHint} />
            <Field ctx={ctx} name={`experiences.${i}.end`} label={f("end")} hint={dateHint} />
            <Field ctx={ctx} name={`experiences.${i}.location`} label={f("location")} />
            <Field
              ctx={ctx}
              name={`experiences.${i}.employment_type`}
              label={f("employment_type")}
            />
            <label className="flex items-center gap-2 text-sm sm:col-span-2">
              <input
                type="checkbox"
                className="size-4 accent-primary"
                {...register(`experiences.${i}.current`)}
              />
              {f("current")}
            </label>
            <Bullets
              ctx={ctx}
              control={control}
              name={`experiences.${i}.bullets`}
              label={f("bullets")}
            />
            <Field
              ctx={ctx}
              name={`experiences.${i}.technologies`}
              label={f("technologies")}
              hint={t("hints.list")}
              className="sm:col-span-2"
            />
          </ItemCard>
        ))}
      </ListBlock>

      <ListBlock
        title={t("sections.education")}
        addLabel={t("add.education")}
        count={edu.fields.length}
        onAdd={() =>
          edu.append({
            id: "",
            institution: "",
            degree: "",
            field: "",
            location: "",
            start: "",
            end: "",
            grade: "",
            details: [],
          })
        }
      >
        {edu.fields.map((item, i) => (
          <ItemCard
            key={item.key}
            heading={heading(
              watch(`education.${i}.degree`),
              watch(`education.${i}.institution`),
              i,
            )}
            index={i}
            total={edu.fields.length}
            onMove={edu.move}
            onRemove={() => edu.remove(i)}
          >
            <Field
              ctx={ctx}
              name={`education.${i}.degree`}
              label={f("degree")}
              warnKey={`${item.id}:degree`}
            />
            <Field
              ctx={ctx}
              name={`education.${i}.institution`}
              label={f("institution")}
              warnKey={`${item.id}:institution`}
            />
            <Field ctx={ctx} name={`education.${i}.field`} label={f("field")} />
            <Field ctx={ctx} name={`education.${i}.location`} label={f("location")} />
            <Field ctx={ctx} name={`education.${i}.start`} label={f("start")} hint={dateHint} />
            <Field ctx={ctx} name={`education.${i}.end`} label={f("end")} hint={dateHint} />
            <Field ctx={ctx} name={`education.${i}.grade`} label={f("grade")} />
            <Bullets
              ctx={ctx}
              control={control}
              name={`education.${i}.details`}
              label={f("details")}
            />
          </ItemCard>
        ))}
      </ListBlock>

      <ListBlock
        title={t("sections.projects")}
        addLabel={t("add.projects")}
        count={prj.fields.length}
        onAdd={() =>
          prj.append({
            id: "",
            name: "",
            role: "",
            url: "",
            start: "",
            end: "",
            description: "",
            bullets: [],
            technologies: "",
          })
        }
      >
        {prj.fields.map((item, i) => (
          <ItemCard
            key={item.key}
            heading={heading(watch(`projects.${i}.name`), watch(`projects.${i}.role`), i)}
            index={i}
            total={prj.fields.length}
            onMove={prj.move}
            onRemove={() => prj.remove(i)}
          >
            <Field
              ctx={ctx}
              name={`projects.${i}.name`}
              label={f("name")}
              warnKey={`${item.id}:name`}
            />
            <Field ctx={ctx} name={`projects.${i}.role`} label={f("role")} />
            <Field ctx={ctx} name={`projects.${i}.start`} label={f("start")} hint={dateHint} />
            <Field ctx={ctx} name={`projects.${i}.end`} label={f("end")} hint={dateHint} />
            <Field
              ctx={ctx}
              name={`projects.${i}.url`}
              label={f("url")}
              type="url"
              className="sm:col-span-2"
            />
            <Field
              ctx={ctx}
              name={`projects.${i}.description`}
              label={f("description")}
              warnKey={`${item.id}:description`}
              multiline
              className="sm:col-span-2"
            />
            <Bullets
              ctx={ctx}
              control={control}
              name={`projects.${i}.bullets`}
              label={f("bullets")}
            />
            <Field
              ctx={ctx}
              name={`projects.${i}.technologies`}
              label={f("technologies")}
              hint={t("hints.list")}
              className="sm:col-span-2"
            />
          </ItemCard>
        ))}
      </ListBlock>

      <ListBlock
        title={t("sections.skills")}
        addLabel={t("add.skills")}
        count={skl.fields.length}
        onAdd={() => skl.append({ id: "", name: "", kind: "hard", skills: "" })}
      >
        {skl.fields.map((item, i) => (
          <ItemCard
            key={item.key}
            heading={heading(watch(`skills.${i}.name`), undefined, i)}
            index={i}
            total={skl.fields.length}
            onMove={skl.move}
            onRemove={() => skl.remove(i)}
          >
            <Field ctx={ctx} name={`skills.${i}.name`} label={f("groupName")} />
            <SelectField ctx={ctx} name={`skills.${i}.kind`} label={f("kind")} options={kinds} />
            <Field
              ctx={ctx}
              name={`skills.${i}.skills`}
              label={f("skills")}
              hint={t("hints.list")}
              warnKey={`${item.id}:skills`}
              multiline
              className="sm:col-span-2"
            />
          </ItemCard>
        ))}
      </ListBlock>

      <ListBlock
        title={t("sections.languages")}
        addLabel={t("add.languages")}
        count={lng.fields.length}
        onAdd={() => lng.append({ id: "", name: "", level: "", cefr: "" })}
      >
        {lng.fields.map((item, i) => (
          <ItemCard
            key={item.key}
            heading={heading(watch(`languages.${i}.name`), watch(`languages.${i}.level`), i)}
            index={i}
            total={lng.fields.length}
            onMove={lng.move}
            onRemove={() => lng.remove(i)}
          >
            <Field ctx={ctx} name={`languages.${i}.name`} label={f("name")} />
            <Field ctx={ctx} name={`languages.${i}.level`} label={f("level")} />
            <SelectField ctx={ctx} name={`languages.${i}.cefr`} label={f("cefr")} options={cefr} />
          </ItemCard>
        ))}
      </ListBlock>

      <ListBlock
        title={t("sections.certifications")}
        addLabel={t("add.certifications")}
        count={crt.fields.length}
        onAdd={() =>
          crt.append({ id: "", name: "", issuer: "", date: "", url: "", credential_id: "" })
        }
      >
        {crt.fields.map((item, i) => (
          <ItemCard
            key={item.key}
            heading={heading(
              watch(`certifications.${i}.name`),
              watch(`certifications.${i}.issuer`),
              i,
            )}
            index={i}
            total={crt.fields.length}
            onMove={crt.move}
            onRemove={() => crt.remove(i)}
          >
            <Field
              ctx={ctx}
              name={`certifications.${i}.name`}
              label={f("name")}
              warnKey={`${item.id}:name`}
            />
            <Field ctx={ctx} name={`certifications.${i}.issuer`} label={f("issuer")} />
            <Field ctx={ctx} name={`certifications.${i}.date`} label={f("date")} hint={dateHint} />
            <Field
              ctx={ctx}
              name={`certifications.${i}.credential_id`}
              label={f("credential_id")}
            />
            <Field
              ctx={ctx}
              name={`certifications.${i}.url`}
              label={f("url")}
              type="url"
              className="sm:col-span-2"
            />
          </ItemCard>
        ))}
      </ListBlock>

      <ListBlock
        title={t("sections.links")}
        addLabel={t("add.links")}
        count={lnk.fields.length}
        onAdd={() => lnk.append({ id: "", kind: "other", url: "", label: "" })}
      >
        {lnk.fields.map((item, i) => (
          <ItemCard
            key={item.key}
            heading={heading(watch(`links.${i}.url`), undefined, i)}
            index={i}
            total={lnk.fields.length}
            onMove={lnk.move}
            onRemove={() => lnk.remove(i)}
          >
            <SelectField ctx={ctx} name={`links.${i}.kind`} label={f("kind")} options={linkKinds} />
            <Field ctx={ctx} name={`links.${i}.label`} label={f("label")} />
            <Field
              ctx={ctx}
              name={`links.${i}.url`}
              label={f("url")}
              type="url"
              className="sm:col-span-2"
            />
          </ItemCard>
        ))}
      </ListBlock>

      <ListBlock
        title={t("sections.extra_sections")}
        addLabel={t("add.extra_sections")}
        count={ext.fields.length}
        onAdd={() => ext.append({ id: "", title: "", bullets: [] })}
      >
        {ext.fields.map((item, i) => (
          <ItemCard
            key={item.key}
            heading={heading(watch(`extra_sections.${i}.title`), undefined, i)}
            index={i}
            total={ext.fields.length}
            onMove={ext.move}
            onRemove={() => ext.remove(i)}
          >
            <Field
              ctx={ctx}
              name={`extra_sections.${i}.title`}
              label={f("sectionTitle")}
              className="sm:col-span-2"
            />
            <Bullets
              ctx={ctx}
              control={control}
              name={`extra_sections.${i}.bullets`}
              label={f("bullets")}
            />
          </ItemCard>
        ))}
      </ListBlock>

      <div className="sticky bottom-0 z-10 -mx-4 flex flex-wrap items-center justify-end gap-3 border-t bg-background/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
        {isDirty ? <span className="mr-auto text-sm text-signal">{t("unsaved")}</span> : null}
        {isDirty ? (
          <Button type="button" variant="ghost" onClick={() => reset(defaults)}>
            {t("reset")}
          </Button>
        ) : null}
        <Button type="submit" disabled={save.isPending || (mode === "master" && !isDirty)}>
          {save.isPending ? t("saving") : t("save")}
        </Button>
      </div>
    </form>
  );
}
