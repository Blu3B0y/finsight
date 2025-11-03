// src/server/actions/accountActions.ts
import { prisma } from "../../lib/prisma";
import { z } from "zod";
s
export const createOrSyncUserSchema = z.object({
  clerkId: z.string(),
  email: z.string().email().optional(),
  name: z.string().optional(),
});

export async function createOrSyncUser(input: z.infer<typeof createOrSyncUserSchema>) {
  const { clerkId, email, name } = createOrSyncUserSchema.parse(input);

  // create user if not exists, or update email/name
  const user = await prisma.user.upsert({
    where: { clerkId },
    update: { email, name },
    create: { clerkId, email, name },
  });
  return user;
}

export const createAccountSchema = z.object({
  clerkId: z.string().optional(), // prefer resolved server-side; but accept for server testing
  userId: z.string().optional(),
  name: z.string(),
  currency: z.string(),
  isDefault: z.boolean().optional(),
});

export async function createAccount(input: z.infer<typeof createAccountSchema>) {
  const { clerkId, userId, name, currency, isDefault } = createAccountSchema.parse(input);

  // resolve userId if clerkId provided
  let uid = userId;
  if (!uid && clerkId) {
    const u = await prisma.user.findUnique({ where: { clerkId } });
    if (!u) throw new Error("User not found for clerkId");
    uid = u.id;
  }
  if (!uid) throw new Error("userId or clerkId required");

  if (isDefault) {
    // transaction: unset previous default and create new
    return prisma.$transaction([
      prisma.account.updateMany({ where: { userId: uid, isDefault: true }, data: { isDefault: false } }),
      prisma.account.create({ data: { userId: uid, name, currency, isDefault } }),
    ]);
  }

  return prisma.account.create({ data: { userId: uid, name, currency, isDefault: isDefault ?? false } });
}

export async function updateDefaultAccount({ clerkId, accountId }: { clerkId?: string, accountId: string }) {
  // resolve userId
  let user = null;
  if (clerkId) {
    user = await prisma.user.findUnique({ where: { clerkId } });
    if (!user) throw new Error("User not found");
  } else {
    // fetch account to get userId
    const acct = await prisma.account.findUnique({ where: { id: accountId } });
    if (!acct) throw new Error("Account not found");
    user = await prisma.user.findUnique({ where: { id: acct.userId } });
  }
  if (!user) throw new Error("Unable to resolve user");

  return prisma.$transaction(async (tx) => {
    await tx.account.updateMany({ where: { userId: user.id, isDefault: true }, data: { isDefault: false } });
    return tx.account.update({ where: { id: accountId }, data: { isDefault: true } });
  });
}
