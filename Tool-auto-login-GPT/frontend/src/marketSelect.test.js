import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  WORLD_MARKETS,
  DEFAULT_MARKET_TIMEZONE,
  DEFAULT_MARKET,
  findMarketByTimezone,
  filterMarkets,
  getMarketLocalTime,
  normalizeSearchText
} from './marketConstants.js'

test('WORLD_MARKETS contains major global target markets', () => {
  assert.ok(WORLD_MARKETS.length >= 25, 'Should have at least 25 major world markets')

  // North America
  assert.ok(WORLD_MARKETS.some(m => m.timezone === 'America/New_York'), 'Must contain US East (New York)')
  assert.ok(WORLD_MARKETS.some(m => m.timezone === 'America/Los_Angeles'), 'Must contain US West (California/LA)')
  assert.ok(WORLD_MARKETS.some(m => m.timezone === 'America/Chicago'), 'Must contain US Central (Chicago/Texas)')
  assert.ok(WORLD_MARKETS.some(m => m.timezone === 'America/Toronto'), 'Must contain Canada East')

  // Europe
  assert.ok(WORLD_MARKETS.some(m => m.timezone === 'Europe/London'), 'Must contain UK (London)')
  assert.ok(WORLD_MARKETS.some(m => m.timezone === 'Europe/Berlin'), 'Must contain Germany (Berlin)')
  assert.ok(WORLD_MARKETS.some(m => m.timezone === 'Europe/Paris'), 'Must contain France (Paris)')

  // Asia - Pacific
  assert.ok(WORLD_MARKETS.some(m => m.timezone === 'Asia/Ho_Chi_Minh'), 'Must contain Vietnam')
  assert.ok(WORLD_MARKETS.some(m => m.timezone === 'Asia/Tokyo'), 'Must contain Japan (Tokyo)')
  assert.ok(WORLD_MARKETS.some(m => m.timezone === 'Asia/Seoul'), 'Must contain South Korea (Seoul)')
  assert.ok(WORLD_MARKETS.some(m => m.timezone === 'Australia/Sydney'), 'Must contain Australia East')
})

test('Default market is Vietnam (Asia/Ho_Chi_Minh)', () => {
  assert.equal(DEFAULT_MARKET_TIMEZONE, 'Asia/Ho_Chi_Minh')
  assert.equal(DEFAULT_MARKET.timezone, 'Asia/Ho_Chi_Minh')
  assert.equal(DEFAULT_MARKET.flag, '🇻🇳')

  // Empty string, null, undefined fallback to Vietnam
  assert.equal(findMarketByTimezone('').timezone, 'Asia/Ho_Chi_Minh')
  assert.equal(findMarketByTimezone(null).timezone, 'Asia/Ho_Chi_Minh')
  assert.equal(findMarketByTimezone(undefined).timezone, 'Asia/Ho_Chi_Minh')
})

test('findMarketByTimezone resolves known timezones and handles custom timezones', () => {
  const ny = findMarketByTimezone('America/New_York')
  assert.equal(ny.id, 'us-east')
  assert.equal(ny.flag, '🇺🇸')

  const tokyo = findMarketByTimezone('Asia/Tokyo')
  assert.equal(tokyo.id, 'jp')
  assert.equal(tokyo.flag, '🇯🇵')

  // Custom obscure timezone
  const custom = findMarketByTimezone('Pacific/Guam')
  assert.equal(custom.id, 'custom')
  assert.equal(custom.flag, '🌐')
  assert.equal(custom.timezone, 'Pacific/Guam')
})

test('filterMarkets supports real-time search with Vietnamese accents and English synonyms', () => {
  // Empty query returns all
  assert.equal(filterMarkets('').length, WORLD_MARKETS.length)

  // Vietnamese accent insensitive
  const myResults = filterMarkets('mỹ')
  assert.ok(myResults.length >= 3, 'Searching "mỹ" should return US regions')
  const myNoAccent = filterMarkets('my')
  assert.equal(myNoAccent.length, myResults.length, '"my" and "mỹ" should yield same count')

  const ducResults = filterMarkets('đức')
  assert.ok(ducResults.some(m => m.timezone === 'Europe/Berlin'), 'Searching "đức" should find Germany')
  const ducNoAccent = filterMarkets('duc')
  assert.ok(ducNoAccent.some(m => m.timezone === 'Europe/Berlin'), 'Searching "duc" should find Germany')

  // English synonyms & cities
  const nyResults = filterMarkets('New York')
  assert.ok(nyResults.some(m => m.timezone === 'America/New_York'))

  const tokyoResults = filterMarkets('tokyo')
  assert.ok(tokyoResults.some(m => m.timezone === 'Asia/Tokyo'))

  const ukResults = filterMarkets('uk')
  assert.ok(ukResults.some(m => m.timezone === 'Europe/London'))
})

test('getMarketLocalTime returns valid local time and day/night status', () => {
  const vnTime = getMarketLocalTime('Asia/Ho_Chi_Minh')
  assert.match(vnTime.timeStr, /^\d{2}:\d{2}\s+(AM|PM)$/)
  assert.equal(typeof vnTime.isDaytime, 'boolean')
  assert.ok(vnTime.statusLabel.includes('☀️') || vnTime.statusLabel.includes('🌙'))
})

test('ChannelManager and YouTubeChannelSettings integrate MarketSelect for publication_timezone', () => {
  const channelManagerSource = readFileSync(
    new URL('./ChannelManager.jsx', import.meta.url),
    'utf8'
  )
  const settingsSource = readFileSync(
    new URL('./YouTubeChannelSettings.jsx', import.meta.url),
    'utf8'
  )
  const trustBuilderSource = readFileSync(
    new URL('./TrustBuilder.jsx', import.meta.url),
    'utf8'
  )

  // ChannelManager imports and uses MarketSelect
  assert.match(channelManagerSource, /import MarketSelect from ['"]\.\/MarketSelect['"]/)
  assert.match(channelManagerSource, /<MarketSelect/)
  assert.match(channelManagerSource, /publication_timezone/)

  // YouTubeChannelSettings imports and uses MarketSelect
  assert.match(settingsSource, /import MarketSelect from ['"]\.\/MarketSelect['"]/)
  assert.match(settingsSource, /<MarketSelect/)
  assert.match(settingsSource, /publication_timezone/)

  // TrustBuilder imports marketConstants and displays market pill
  assert.match(trustBuilderSource, /findMarketByTimezone/)
  assert.match(trustBuilderSource, /getMarketLocalTime/)
  assert.match(trustBuilderSource, /trust-market-pill/)
})
