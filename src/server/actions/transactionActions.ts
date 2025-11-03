// src/server/actions/transactionActions.ts
import { prisma } from "@/lib/prisma";
import { z } from "zod";

export const createTransactionSchema = z.object({
  accountId: z.string(),
  type: z.enum(["INCOME", "EXPENSE"]),
  amountCents: z.preprocess(a => BigInt(a as any), z.bigint()),
  description: z.string().optional(),
  category: z.string().optional(),
  date: z.string(), // ISO string
  recurring: z.boolean().optional(),
  recurringInterval: z.string().optional(),
});

export async function createTransaction(input: z.infer<typeof createTransactionSchema>) {
  const data = createTransactionSchema.parse(input);
  const accountId = data.accountId;

  // atomic create + adjust account balance
  return prisma.$transaction(async (tx) => {
    const t = await tx.transaction.create({
      data: {
        accountId,
        type: data.type,
        amountCents: data.amountCents,
        description: data.description,
        category: data.category,
        date: new Date(data.date),
        recurring: data.recurring ?? false,
        recurringInterval: data.recurringInterval,
      },
    });

    // adjust balance: income adds, expense subtracts
    const delta = data.type === "INCOME" ? data.amountCents : -data.amountCents;
    // Prisma BigInt increment requires raw usage in some environments; using update with increment
    await tx.account.update({
      where: { id: accountId },
      data: { balanceCents: { increment: delta } as any },
    });

    return t;
  });
}

export const updateTransactionSchema = z.object({
  transactionId: z.string(),
  type: z.enum(["INCOME", "EXPENSE"]).optional(),
  amountCents: z.preprocess(a => (a !== undefined ? BigInt(a as any) : undefined), z.optional(z.bigint())),
  description: z.string().optional(),
  category: z.string().optional(),
  date: z.string().optional(),
  recurring: z.boolean().optional(),
  recurringInterval: z.string().optional(),
});

export async function updateTransaction(input: z.infer<typeof updateTransactionSchema>) {
  const { transactionId, ...rest } = updateTransactionSchema.parse(input);

  // load old transaction to compute balance diff
  return prisma.$transaction(async (tx) => {
    const old = await tx.transaction.findUnique({ where: { id: transactionId } });
    if (!old) throw new Error("Transaction not found");

    // compute old effect on balance
    const oldEffect = old.type === "INCOME" ? old.amountCents : -old.amountCents;

    // new values
    const updated = await tx.transaction.update({
      where: { id: transactionId },
      data: {
        ...(rest.type !== undefined && { type: rest.type }),
        ...(rest.amountCents !== undefined && { amountCents: rest.amountCents as any }),
        ...(rest.description !== undefined && { description: rest.description }),
        ...(rest.category !== undefined && { category: rest.category }),
        ...(rest.date !== undefined && { date: rest.date ? new Date(rest.date) : undefined }),
        ...(rest.recurring !== undefined && { recurring: rest.recurring }),
        ...(rest.recurringInterval !== undefined && { recurringInterval: rest.recurringInterval }),
      },
    });

    const newEffect = (updated.type === "INCOME" ? updated.amountCents : -updated.amountCents);
    const diff = (newEffect as bigint) - (oldEffect as bigint);

    if (diff !== BigInt(0)) {
      await tx.account.update({ where: { id: updated.accountId }, data: { balanceCents: { increment: diff } as any } });
    }

    return updated;
  });
}

export async function bulkDeleteTransactions({ transactionIds }: { transactionIds: string[] }) {
  return prisma.$transaction(async (tx) => {
    const txns = await tx.transaction.findMany({ where: { id: { in: transactionIds } } });

    // compute balance corrections per account
    const corrections = txns.reduce<Record<string,bigint>>((acc, t) => {
      const delta = t.type === "INCOME" ? -t.amountCents : t.amountCents;
      acc[t.accountId] = (acc[t.accountId] || BigInt(0)) + (delta as bigint);
      return acc;
    }, {});

    // apply balance updates
    for (const [accountId, delta] of Object.entries(corrections)) {
      await tx.account.update({ where: { id: accountId }, data: { balanceCents: { increment: delta } as any } });
    }

    await tx.transaction.deleteMany({ where: { id: { in: transactionIds } } });
    return { deleted: transactionIds.length };
  });
}
