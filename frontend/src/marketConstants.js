// marketConstants.js - Global Target Markets Registry for Nexus Studio Engine
// Single source of truth for channel timezone, regional golden hours, and Trust Builder behavior.

export const DEFAULT_MARKET_TIMEZONE = 'Asia/Ho_Chi_Minh'

export const WORLD_MARKETS = [
  // --- CHÂU Á - THÁI BÌNH DƯƠNG ---
  {
    id: 'vn',
    name: 'Việt Nam (Mặc định)',
    country: 'Việt Nam',
    region: 'Châu Á - TBD',
    flag: '🇻🇳',
    cities: 'Hà Nội, TP. Hồ Chí Minh, Đà Nẵng',
    timezone: 'Asia/Ho_Chi_Minh',
    keywords: ['vn', 'viet nam', 'vietnam', 'hanoi', 'ha noi', 'hcm', 'sai gon', 'saigon', 'gmt+7', 'ict', 'mac dinh']
  },
  {
    id: 'jp',
    name: 'Nhật Bản',
    country: 'Nhật Bản',
    region: 'Châu Á - TBD',
    flag: '🇯🇵',
    cities: 'Tokyo, Osaka, Kyoto, Yokohama',
    timezone: 'Asia/Tokyo',
    keywords: ['nhat ban', 'nhat', 'japan', 'jp', 'tokyo', 'osaka', 'jst', 'gmt+9']
  },
  {
    id: 'kr',
    name: 'Hàn Quốc',
    country: 'Hàn Quốc',
    region: 'Châu Á - TBD',
    flag: '🇰🇷',
    cities: 'Seoul, Busan, Incheon',
    timezone: 'Asia/Seoul',
    keywords: ['han quoc', 'han', 'korea', 'kr', 'seoul', 'busan', 'kst', 'gmt+9']
  },
  {
    id: 'tw',
    name: 'Đài Loan',
    country: 'Đài Loan',
    region: 'Châu Á - TBD',
    flag: '🇹🇼',
    cities: 'Taipei, Kaohsiung, Taichung',
    timezone: 'Asia/Taipei',
    keywords: ['dai loan', 'taiwan', 'tw', 'taipei', 'cst', 'gmt+8']
  },
  {
    id: 'sg',
    name: 'Singapore',
    country: 'Singapore',
    region: 'Châu Á - TBD',
    flag: '🇸🇬',
    cities: 'Singapore',
    timezone: 'Asia/Singapore',
    keywords: ['singapore', 'sing', 'sg', 'sgt', 'gmt+8']
  },
  {
    id: 'th',
    name: 'Thái Lan',
    country: 'Thái Lan',
    region: 'Châu Á - TBD',
    flag: '🇹🇭',
    cities: 'Bangkok, Chiang Mai, Phuket',
    timezone: 'Asia/Bangkok',
    keywords: ['thai lan', 'thai', 'thailand', 'th', 'bangkok', 'ict', 'gmt+7']
  },
  {
    id: 'id',
    name: 'Indonesia',
    country: 'Indonesia',
    region: 'Châu Á - TBD',
    flag: '🇮🇩',
    cities: 'Jakarta, Surabaya, Bandung',
    timezone: 'Asia/Jakarta',
    keywords: ['indonesia', 'indo', 'id', 'jakarta', 'wib', 'gmt+7']
  },
  {
    id: 'ph',
    name: 'Philippines',
    country: 'Philippines',
    region: 'Châu Á - TBD',
    flag: '🇵🇭',
    cities: 'Manila, Quezon City, Cebu',
    timezone: 'Asia/Manila',
    keywords: ['philippines', 'philip', 'ph', 'manila', 'pht', 'gmt+8']
  },
  {
    id: 'in',
    name: 'Ấn Độ',
    country: 'Ấn Độ',
    region: 'Châu Á - TBD',
    flag: '🇮🇳',
    cities: 'New Delhi, Mumbai, Bengaluru',
    timezone: 'Asia/Kolkata',
    keywords: ['an do', 'india', 'in', 'delhi', 'new delhi', 'mumbai', 'ist', 'gmt+5:30']
  },
  {
    id: 'au-east',
    name: 'Úc (Australia) - Miền Đông',
    country: 'Úc',
    region: 'Châu Á - TBD',
    flag: '🇦🇺',
    cities: 'Sydney, Melbourne, Brisbane, Canberra',
    timezone: 'Australia/Sydney',
    keywords: ['uc', 'australia', 'au', 'sydney', 'melbourne', 'brisbane', 'aest', 'aedt']
  },
  {
    id: 'au-west',
    name: 'Úc (Australia) - Miền Tây',
    country: 'Úc',
    region: 'Châu Á - TBD',
    flag: '🇦🇺',
    cities: 'Perth, Fremantle',
    timezone: 'Australia/Perth',
    keywords: ['uc', 'australia', 'au', 'perth', 'awst']
  },
  {
    id: 'nz',
    name: 'New Zealand',
    country: 'New Zealand',
    region: 'Châu Á - TBD',
    flag: '🇳🇿',
    cities: 'Auckland, Wellington, Christchurch',
    timezone: 'Pacific/Auckland',
    keywords: ['new zealand', 'nz', 'auckland', 'wellington', 'nzst', 'nzdt']
  },

  // --- BẮC MỸ (USA, CANADA, MEXICO) ---
  {
    id: 'us-east',
    name: 'Hoa Kỳ (Mỹ) - Bờ Đông',
    country: 'Hoa Kỳ',
    region: 'Bắc Mỹ',
    flag: '🇺🇸',
    cities: 'New York, Florida, Washington D.C., Boston, Atlanta',
    timezone: 'America/New_York',
    keywords: ['my', 'hoa ky', 'us', 'usa', 'united states', 'new york', 'ny', 'florida', 'dc', 'boston', 'est', 'edt', 'bo dong']
  },
  {
    id: 'us-west',
    name: 'Hoa Kỳ (Mỹ) - Bờ Tây',
    country: 'Hoa Kỳ',
    region: 'Bắc Mỹ',
    flag: '🇺🇸',
    cities: 'California, Los Angeles, San Francisco, Seattle',
    timezone: 'America/Los_Angeles',
    keywords: ['my', 'hoa ky', 'us', 'usa', 'united states', 'california', 'cali', 'la', 'los angeles', 'san francisco', 'seattle', 'pst', 'pdt', 'bo tay']
  },
  {
    id: 'us-central',
    name: 'Hoa Kỳ (Mỹ) - Miền Trung',
    country: 'Hoa Kỳ',
    region: 'Bắc Mỹ',
    flag: '🇺🇸',
    cities: 'Texas, Chicago, Houston, Dallas, Austin',
    timezone: 'America/Chicago',
    keywords: ['my', 'hoa ky', 'us', 'usa', 'united states', 'texas', 'chicago', 'houston', 'dallas', 'cst', 'cdt', 'mien trung']
  },
  {
    id: 'us-mountain',
    name: 'Hoa Kỳ (Mỹ) - Vùng Núi',
    country: 'Hoa Kỳ',
    region: 'Bắc Mỹ',
    flag: '🇺🇸',
    cities: 'Denver, Phoenix, Salt Lake City, Colorado',
    timezone: 'America/Denver',
    keywords: ['my', 'hoa ky', 'us', 'usa', 'denver', 'phoenix', 'arizona', 'colorado', 'mst', 'mdt', 'vung nui']
  },
  {
    id: 'us-alaska',
    name: 'Hoa Kỳ (Mỹ) - Alaska',
    country: 'Hoa Kỳ',
    region: 'Bắc Mỹ',
    flag: '🇺🇸',
    cities: 'Anchorage, Fairbanks, Juneau',
    timezone: 'America/Anchorage',
    keywords: ['my', 'alaska', 'anchorage', 'akst', 'akdt']
  },
  {
    id: 'us-hawaii',
    name: 'Hoa Kỳ (Mỹ) - Hawaii',
    country: 'Hoa Kỳ',
    region: 'Bắc Mỹ',
    flag: '🇺🇸',
    cities: 'Honolulu, Maui, Oahu',
    timezone: 'Pacific/Honolulu',
    keywords: ['my', 'hawaii', 'honolulu', 'hst']
  },
  {
    id: 'ca-east',
    name: 'Canada - Miền Đông',
    country: 'Canada',
    region: 'Bắc Mỹ',
    flag: '🇨🇦',
    cities: 'Toronto, Montreal, Ottawa, Quebec',
    timezone: 'America/Toronto',
    keywords: ['canada', 'ca', 'toronto', 'montreal', 'ottawa', 'est', 'edt']
  },
  {
    id: 'ca-west',
    name: 'Canada - Miền Tây',
    country: 'Canada',
    region: 'Bắc Mỹ',
    flag: '🇨🇦',
    cities: 'Vancouver, Victoria, Calgary, Edmonton',
    timezone: 'America/Vancouver',
    keywords: ['canada', 'ca', 'vancouver', 'calgary', 'pst', 'pdt']
  },
  {
    id: 'mx',
    name: 'Mexico',
    country: 'Mexico',
    region: 'Bắc Mỹ',
    flag: '🇲🇽',
    cities: 'Mexico City, Guadalajara, Monterrey',
    timezone: 'America/Mexico_City',
    keywords: ['mexico', 'mx', 'mexico city', 'cst']
  },

  // --- CHÂU ÂU (EUROPE) ---
  {
    id: 'uk',
    name: 'Vương Quốc Anh (UK)',
    country: 'Vương Quốc Anh',
    region: 'Châu Âu',
    flag: '🇬🇧',
    cities: 'London, Manchester, Birmingham, Edinburgh',
    timezone: 'Europe/London',
    keywords: ['anh', 'uk', 'united kingdom', 'great britain', 'england', 'london', 'manchester', 'gmt', 'bst']
  },
  {
    id: 'de',
    name: 'Đức (Germany)',
    country: 'Đức',
    region: 'Châu Âu',
    flag: '🇩🇪',
    cities: 'Berlin, Frankfurt, Munich, Hamburg',
    timezone: 'Europe/Berlin',
    keywords: ['duc', 'germany', 'de', 'berlin', 'frankfurt', 'munich', 'cet', 'cest']
  },
  {
    id: 'fr',
    name: 'Pháp (France)',
    country: 'Pháp',
    region: 'Châu Âu',
    flag: '🇫🇷',
    cities: 'Paris, Lyon, Marseille, Nice',
    timezone: 'Europe/Paris',
    keywords: ['phap', 'france', 'fr', 'paris', 'lyon', 'cet', 'cest']
  },
  {
    id: 'nl',
    name: 'Hà Lan (Netherlands)',
    country: 'Hà Lan',
    region: 'Châu Âu',
    flag: '🇳🇱',
    cities: 'Amsterdam, Rotterdam, The Hague',
    timezone: 'Europe/Amsterdam',
    keywords: ['ha lan', 'netherlands', 'holland', 'nl', 'amsterdam', 'cet', 'cest']
  },
  {
    id: 'it',
    name: 'Ý (Italy)',
    country: 'Ý',
    region: 'Châu Âu',
    flag: '🇮🇹',
    cities: 'Rome, Milan, Naples, Florence',
    timezone: 'Europe/Rome',
    keywords: ['y', 'italy', 'italia', 'it', 'rome', 'milan', 'cet', 'cest']
  },
  {
    id: 'es',
    name: 'Tây Ban Nha (Spain)',
    country: 'Tây Ban Nha',
    region: 'Châu Âu',
    flag: '🇪🇸',
    cities: 'Madrid, Barcelona, Valencia, Seville',
    timezone: 'Europe/Madrid',
    keywords: ['tay ban nha', 'spain', 'espana', 'es', 'madrid', 'barcelona', 'cet', 'cest']
  },
  {
    id: 'pl',
    name: 'Ba Lan (Poland)',
    country: 'Ba Lan',
    region: 'Châu Âu',
    flag: '🇵🇱',
    cities: 'Warsaw, Krakow, Wroclaw',
    timezone: 'Europe/Warsaw',
    keywords: ['ba lan', 'poland', 'polska', 'pl', 'warsaw', 'cet', 'cest']
  },
  {
    id: 'se',
    name: 'Thụy Điển (Sweden)',
    country: 'Thụy Điển',
    region: 'Châu Âu',
    flag: '🇸🇪',
    cities: 'Stockholm, Gothenburg, Malmo',
    timezone: 'Europe/Stockholm',
    keywords: ['thuy dien', 'sweden', 'se', 'stockholm', 'cet', 'cest']
  },
  {
    id: 'ch',
    name: 'Thụy Sĩ (Switzerland)',
    country: 'Thụy Sĩ',
    region: 'Châu Âu',
    flag: '🇨🇭',
    cities: 'Zurich, Geneva, Bern, Basel',
    timezone: 'Europe/Zurich',
    keywords: ['thuy si', 'switzerland', 'swiss', 'ch', 'zurich', 'geneva', 'cet', 'cest']
  },
  {
    id: 'ru',
    name: 'Nga (Russia)',
    country: 'Nga',
    region: 'Châu Âu',
    flag: '🇷🇺',
    cities: 'Moscow, Saint Petersburg, Novosibirsk',
    timezone: 'Europe/Moscow',
    keywords: ['nga', 'russia', 'ru', 'moscow', 'msk']
  },

  // --- TRUNG ĐÔNG, MỸ LATIN & CHÂU PHI ---
  {
    id: 'ae',
    name: 'UAE (Ả Rập Thống Nhất)',
    country: 'UAE',
    region: 'Trung Đông',
    flag: '🇦🇪',
    cities: 'Dubai, Abu Dhabi, Sharjah',
    timezone: 'Asia/Dubai',
    keywords: ['uae', 'dubai', 'abu dhabi', 'arrap', 'a rap', 'gst', 'gmt+4']
  },
  {
    id: 'sa',
    name: 'Ả Rập Xê Út (Saudi Arabia)',
    country: 'Ả Rập Xê Út',
    region: 'Trung Đông',
    flag: '🇸🇦',
    cities: 'Riyadh, Jeddah, Mecca',
    timezone: 'Asia/Riyadh',
    keywords: ['saudi', 'saudi arabia', 'riyadh', 'a rap xe ut', 'ast']
  },
  {
    id: 'br',
    name: 'Brazil',
    country: 'Brazil',
    region: 'Mỹ Latin',
    flag: '🇧🇷',
    cities: 'Sao Paulo, Rio de Janeiro, Brasilia',
    timezone: 'America/Sao_Paulo',
    keywords: ['brazil', 'brasil', 'br', 'sao paulo', 'rio', 'brt']
  },
  {
    id: 'ar',
    name: 'Argentina',
    country: 'Argentina',
    region: 'Mỹ Latin',
    flag: '🇦🇷',
    cities: 'Buenos Aires, Cordoba, Rosario',
    timezone: 'America/Argentina/Buenos_Aires',
    keywords: ['argentina', 'ar', 'buenos aires', 'art']
  },
  {
    id: 'za',
    name: 'Nam Phi (South Africa)',
    country: 'Nam Phi',
    region: 'Châu Phi',
    flag: '🇿🇦',
    cities: 'Johannesburg, Cape Town, Durban',
    timezone: 'Africa/Johannesburg',
    keywords: ['nam phi', 'south africa', 'za', 'johannesburg', 'cape town', 'sast']
  }
]

export const DEFAULT_MARKET = WORLD_MARKETS[0] // Việt Nam

/**
 * Normalizes text by removing Vietnamese accents, lowering case, and trimming.
 */
export function normalizeSearchText(str) {
  if (!str) return ''
  return str
    .toString()
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/đ/g, 'd')
    .replace(/Đ/g, 'd')
    .trim()
}

/**
 * Finds a market by its IANA timezone string.
 * Falls back to DEFAULT_MARKET if not provided, or returns a custom object if unrecognized.
 */
export function findMarketByTimezone(timezone) {
  const normalizedTz = (timezone || '').trim()
  if (!normalizedTz) {
    return DEFAULT_MARKET
  }
  const match = WORLD_MARKETS.find(m => m.timezone.toLowerCase() === normalizedTz.toLowerCase())
  if (match) {
    return match
  }
  // Unknown / Custom IANA timezone
  return {
    id: 'custom',
    name: `Tùy chỉnh (${normalizedTz})`,
    country: 'Tùy chỉnh',
    region: 'Khác',
    flag: '🌐',
    cities: normalizedTz,
    timezone: normalizedTz,
    keywords: [normalizedTz.toLowerCase(), 'custom', 'tuy chinh']
  }
}

/**
 * Filters the WORLD_MARKETS array matching a search query.
 * Matches name, country, cities, timezone, and keywords (case and accent insensitive).
 */
export function filterMarkets(searchQuery) {
  const query = normalizeSearchText(searchQuery)
  if (!query) {
    return WORLD_MARKETS
  }
  return WORLD_MARKETS.filter(market => {
    if (normalizeSearchText(market.name).includes(query)) return true
    if (normalizeSearchText(market.country).includes(query)) return true
    if (normalizeSearchText(market.cities).includes(query)) return true
    if (normalizeSearchText(market.timezone).includes(query)) return true
    if (market.keywords.some(kw => normalizeSearchText(kw).includes(query))) return true
    return false
  })
}

/**
 * Returns real-time local time info in the specified IANA timezone.
 * Returns { timeStr: '14:35', dateStr: '11/10', isDaytime: boolean, fullText: '14:35 (Đang thức)' }
 */
export function getMarketLocalTime(timezone) {
  const targetTz = timezone || DEFAULT_MARKET_TIMEZONE
  try {
    const now = new Date()
    const timeFormatter = new Intl.DateTimeFormat('en-US', {
      timeZone: targetTz,
      hour: '2-digit',
      minute: '2-digit',
      hour12: true
    })
    const hourFormatter = new Intl.DateTimeFormat('en-US', {
      timeZone: targetTz,
      hour: 'numeric',
      hour12: false
    })
    const timeStr = timeFormatter.format(now)
    const currentHour = parseInt(hourFormatter.format(now), 10)
    // Active / Daytime in Trust Builder is from 08:00 to 23:00
    const isDaytime = currentHour >= 8 && currentHour < 23

    return {
      timeStr,
      isDaytime,
      statusLabel: isDaytime ? '☀️ Đang hoạt động' : '🌙 Đang nghỉ ngơi',
      currentHour
    }
  } catch {
    return {
      timeStr: '--:--',
      isDaytime: true,
      statusLabel: 'Chưa rõ',
      currentHour: 12
    }
  }
}
