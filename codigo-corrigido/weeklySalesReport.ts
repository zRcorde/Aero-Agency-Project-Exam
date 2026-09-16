import { ShopifyClient, ShopifyApiError, type Order, type Product, type Shop } from "../lib/shopify";
import { emailProvider } from "../lib/email";
import { db } from "../lib/db";
import { logger } from "../lib/logger";

const MAX_ATTEMPTS = 3;
const TOP_PRODUCTS_COUNT = 3;
const PRODUCT_FETCH_CONCURRENCY = 5;
const PRODUCT_BATCH_DELAY_MS = 300;

interface WeekRange { start: Date; end: Date; key: string }

interface ProductSales { title: string; quantity: number }

interface SalesSummary {
  totalRevenue: number;
  orderCount: number;
  averageOrderValue: number;
  topProducts: ProductSales[];
}

/** Thrown when one or more products could not be resolved. The report must not go out incomplete. */
class ProductLookupError extends Error {
  constructor(public readonly failedProductIds: string[]) {
    super(`Failed to resolve ${failedProductIds.length} product(s): ${failedProductIds.join(", ")}`);
    this.name = "ProductLookupError";
  }
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Retries transient failures with exponential backoff (+jitter), honouring Shopify's
 * Retry-After when present. Permanent 4xx errors (other than 429) are not retried.
 */
async function withRetry<T>(fn: () => Promise<T>, attempts = MAX_ATTEMPTS): Promise<T> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      const isPermanentClientError =
        err instanceof ShopifyApiError && err.status >= 400 && err.status < 500 && err.status !== 429;
      if (isPermanentClientError) throw err;

      if (attempt < attempts) {
        const retryAfterMs = err instanceof ShopifyApiError ? err.retryAfterMs : undefined;
        const base = retryAfterMs ?? 500 * 2 ** (attempt - 1);
        const jitter = Math.random() * 100;
        await sleep(base + jitter);
      }
    }
  }
  throw lastError;
}

/**
 * Previous calendar week (Monday 00:00:00.000 to Sunday 23:59:59.999) expressed as a UTC
 * instant that corresponds to local wall-clock time in `timeZone`. Uses only Intl (no date
 * library) with one offset-correction pass to stay correct across DST transitions.
 */
function previousWeekRange(timeZone: string, now: Date = new Date()): WeekRange {
  const offsetNow = tzOffsetMs(now, timeZone);
  const localNow = new Date(now.getTime() + offsetNow);
  const daysSinceMonday = (localNow.getUTCDay() + 6) % 7;

  const localStart = Date.UTC(
    localNow.getUTCFullYear(),
    localNow.getUTCMonth(),
    localNow.getUTCDate() - daysSinceMonday - 7,
  );
  const localEnd = localStart + 7 * 24 * 60 * 60 * 1000 - 1;

  const start = new Date(localStart - tzOffsetMs(new Date(localStart - offsetNow), timeZone));
  const end = new Date(localEnd - tzOffsetMs(new Date(localEnd - offsetNow), timeZone));

  return { start, end, key: isoDateInZone(start, timeZone) };
}

/** Offset (ms) such that `date.getTime() + offset` renders as the same wall-clock fields in `timeZone`. */
function tzOffsetMs(date: Date, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(date);

  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? "0");
  const hour = get("hour") % 24;
  const asUtc = Date.UTC(get("year"), get("month") - 1, get("day"), hour, get("minute"), get("second"));
  return asUtc - date.getTime();
}

/** Local calendar date (YYYY-MM-DD) of `date` in `timeZone`, used as the week key. */
function isoDateInZone(date: Date, timeZone: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(
    date,
  );
}

function formatDate(date: Date, timeZone: string): string {
  return new Intl.DateTimeFormat("pt-PT", { dateStyle: "medium", timeZone }).format(date);
}

function formatMoney(amount: number, currency: string): string {
  return new Intl.NumberFormat("pt-PT", { style: "currency", currency }).format(amount);
}

async function loadActiveShops(): Promise<Shop[]> {
  const result = await db.query<Shop>(
    `SELECT id, domain, name, owner_email AS "ownerEmail", currency,
            iana_timezone AS "ianaTimezone", access_token AS "accessToken"
       FROM shops WHERE active = true ORDER BY id`,
  );
  return result.rows;
}

/**
 * Resolves the top products with a bounded concurrency (instead of one Promise.all per line
 * item) and deduplicated product ids. If any product cannot be resolved, throws instead of
 * silently sending an incomplete report, per README: "dados em falta é pior do que não enviar".
 */
async function fetchTopProducts(client: ShopifyClient, orders: Order[]): Promise<ProductSales[]> {
  const lineItems = orders.flatMap((order) => order.lineItems);
  const uniqueProductIds = [...new Set(lineItems.map((item) => item.productId))];

  const products = new Map<string, Product>();
  const failedProductIds: string[] = [];

  for (let i = 0; i < uniqueProductIds.length; i += PRODUCT_FETCH_CONCURRENCY) {
    const batch = uniqueProductIds.slice(i, i + PRODUCT_FETCH_CONCURRENCY);
    const results = await Promise.all(
      batch.map(async (productId) => {
        try {
          return await client.getProduct(productId);
        } catch {
          failedProductIds.push(productId);
          return null;
        }
      }),
    );
    for (const product of results) if (product) products.set(product.id, product);
    if (i + PRODUCT_FETCH_CONCURRENCY < uniqueProductIds.length) await sleep(PRODUCT_BATCH_DELAY_MS);
  }

  if (failedProductIds.length > 0) throw new ProductLookupError(failedProductIds);

  const totals = new Map<string, ProductSales>();
  for (const item of lineItems) {
    const product = products.get(item.productId);
    if (!product) continue;
    const current = totals.get(product.id) ?? { title: product.title, quantity: 0 };
    current.quantity += item.quantity;
    totals.set(product.id, current);
  }
  return [...totals.values()].sort((a, b) => b.quantity - a.quantity).slice(0, TOP_PRODUCTS_COUNT);
}

function buildSummary(orders: Order[], topProducts: ProductSales[]): SalesSummary {
  const totalRevenue = orders.reduce((acc, order) => acc + Number(order.totalPrice), 0);
  const orderCount = orders.length;
  const averageOrderValue = orderCount === 0 ? 0 : totalRevenue / orderCount;
  return { totalRevenue, orderCount, averageOrderValue, topProducts };
}

function renderEmail(shop: Shop, week: WeekRange, s: SalesSummary): string {
  const productRows = s.topProducts.map((p) => `<li>${p.title} — ${p.quantity} un.</li>`).join("");
  const period = `${formatDate(week.start, shop.ianaTimezone)} a ${formatDate(week.end, shop.ianaTimezone)}`;
  return `
    <h2>Resumo semanal — ${shop.name}</h2>
    <p>Semana de ${period}</p>
    <ul>
      <li>Total vendido: ${formatMoney(s.totalRevenue, shop.currency)}</li>
      <li>Encomendas: ${s.orderCount}</li>
      <li>Ticket médio: ${formatMoney(s.averageOrderValue, shop.currency)}</li>
    </ul>
    <h3>Top ${TOP_PRODUCTS_COUNT} produtos</h3>
    <ol>${productRows || "<li>Sem vendas nesta semana</li>"}</ol>
    <p>Bom início de semana!</p>`;
}

/**
 * Atomically reserves the (shop_id, week_start) slot before anything is sent.
 * Requires a unique constraint on report_sends(shop_id, week_start):
 *   ALTER TABLE report_sends ADD CONSTRAINT report_sends_shop_week_unique UNIQUE (shop_id, week_start);
 * Returns false when the slot is already claimed (sent, or in-flight in another run).
 */
async function claimWeek(shopId: number, weekKey: string): Promise<boolean> {
  const result = await db.query<{ shop_id: number }>(
    `INSERT INTO report_sends (shop_id, week_start, sent_at) VALUES ($1, $2, NULL)
       ON CONFLICT (shop_id, week_start) DO NOTHING
       RETURNING shop_id`,
    [shopId, weekKey],
  );
  return result.rows.length > 0;
}

async function markSent(shopId: number, weekKey: string): Promise<void> {
  await db.query(`UPDATE report_sends SET sent_at = now() WHERE shop_id = $1 AND week_start = $2`, [
    shopId,
    weekKey,
  ]);
}

/** Releases a claim that never made it to a successful send, so a future run can retry. */
async function releaseClaim(shopId: number, weekKey: string): Promise<void> {
  await db.query(`DELETE FROM report_sends WHERE shop_id = $1 AND week_start = $2 AND sent_at IS NULL`, [
    shopId,
    weekKey,
  ]);
}

async function sendReport(shop: Shop, week: WeekRange): Promise<void> {
  const client = new ShopifyClient(shop);
  const orders = await client.listOrders({
    createdAtMin: week.start,
    createdAtMax: week.end,
    financialStatus: "paid",
  });
  const summary = buildSummary(orders, await fetchTopProducts(client, orders));

  await emailProvider.send({
    to: shop.ownerEmail,
    subject: `O teu resumo de vendas da semana — ${shop.name}`,
    html: renderEmail(shop, week, summary),
  });

  await markSent(shop.id, week.key);
}

async function processShop(shop: Shop, week: WeekRange): Promise<void> {
  const claimed = await claimWeek(shop.id, week.key);
  if (!claimed) {
    logger.info("Weekly report already sent for shop, skipping", { shopId: shop.id, week: week.key });
    return;
  }
  try {
    await withRetry(() => sendReport(shop, week));
  } catch (err) {
    await releaseClaim(shop.id, week.key);
    throw err;
  }
}

export async function runWeeklySalesReport(): Promise<void> {
  const shops = await loadActiveShops();
  logger.info("Weekly sales report started", { shops: shops.length });

  let sent = 0;
  let failed = 0;
  for (const shop of shops) {
    const week = previousWeekRange(shop.ianaTimezone);
    logger.info("Processing shop", { shopId: shop.id, domain: shop.domain });
    try {
      await processShop(shop, week);
      sent++;
    } catch (err) {
      failed++;
      logger.error("Weekly report failed for shop", { shopId: shop.id, error: String(err) });
    }
  }
  logger.info("Weekly sales report finished", { sent, failed });
}
