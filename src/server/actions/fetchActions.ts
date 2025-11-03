// src/server/actions/fetchActions.ts
import { prisma } from "@/lib/prisma";

export async function getAccounts({ clerkId, userId }: { clerkId?: string, userId?: string }) {
  let uid = userId;
  if (!uid && clerkId) {
    const u = await prisma.user.findUnique({ where: { clerkId } });
    if (!u) return [];
    uid = u.id;
  }
  if (!uid) return [];

  return prisma.account.findMany({
    where: { userId: uid },
    select: { id: true, name: true, currency: true, isDefault: true, balanceCents: true, createdAt: true },
    orderBy: { createdAt: "desc" },
  });
}

export async function getAccountWithTransactions({ accountId, limit = 200 }: { accountId: string, limit?: number }) {
  return prisma.account.findUnique({
    where: { id: accountId },
    include: {
      transactions: { orderBy: { date: "desc" }, take: limit },
    },
  });
}

export async function getDashboardData({ clerkId, userId }: { clerkId?: string, userId?: string }) {
  let uid = userId;
  if (!uid && clerkId) {
    const u = await prisma.user.findUnique({ where: { clerkId } });
    if (!u) return {};
    uid = u.id;
  }
  if (!uid) return {};

  const totalIncome = await prisma.transaction.aggregate({
    where: { account: { userId: uid }, type: "INCOME" },
    _sum: { amountCents: true },
  });
  const totalExpense = await prisma.transaction.aggregate({
    where: { account: { userId: uid }, type: "EXPENSE" },
    _sum: { amountCents: true },
  });

  // simple monthly aggregation using raw SQL for convenience
  const monthly = await prisma.$queryRaw`
    SELECT date_trunc('month', "date") as month, SUM("amountCents") as total, "type"
    FROM "Transaction" t JOIN "Account" a ON t."accountId" = a.id
    WHERE a."userId" = ${uid}
    GROUP BY month, "type"
    ORDER BY month DESC
    LIMIT 12
  `;

  return { totalIncome, totalExpense, monthly };
}
