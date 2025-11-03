// src/seed/seed.js
const { PrismaClient } = require("@prisma/client");
const prisma = new PrismaClient();

async function main() {
  const clerkId = process.env.SEED_CLERK_ID || "seed-clerk-1";

  // create user
  let user = await prisma.user.findUnique({ where: { clerkId }});
  if (!user) {
    user = await prisma.user.create({ data: { clerkId, email: "seed@example.com", name: "Seed User" }});
  }

  // create 2 accounts
  const acct1 = await prisma.account.create({ data: { userId: user.id, name: "Checking", currency: "USD", isDefault: true, balanceCents: 0n }});
  const acct2 = await prisma.account.create({ data: { userId: user.id, name: "Savings", currency: "USD", isDefault: false, balanceCents: 0n }});

  // create mock transactions
  const categories = ["groceries", "salary", "utilities", "entertainment", "transfer"];
  const now = Date.now();
  const txCount = 120;

  const txs = [];
  for (let i = 0; i < txCount; i++) {
    const isIncome = Math.random() < 0.25;
    const amount = Math.floor(Math.random() * (isIncome ? 200000 : 20000)) + 100; // cents
    const account = Math.random() < 0.7 ? acct1 : acct2;
    const date = new Date(now - Math.floor(Math.random() * 1000 * 60 * 60 * 24 * 365)).toISOString();

    txs.push({
      accountId: account.id,
      type: isIncome ? "INCOME" : "EXPENSE",
      amountCents: BigInt(amount),
      description: `${isIncome ? "Income" : "Expense"} ${i}`,
      category: categories[Math.floor(Math.random() * categories.length)],
      date,
      recurring: false,
    });
  }

  for (const t of txs) {
    await prisma.transaction.create({ data: t });
    // update account balance
    const delta = t.type === "INCOME" ? t.amountCents : -t.amountCents;
    await prisma.account.update({ where: { id: t.accountId }, data: { balanceCents: { increment: delta } }});
  }

  console.log("Seeding complete.");
}

main()
  .catch((e) => {
    console.error(e);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
