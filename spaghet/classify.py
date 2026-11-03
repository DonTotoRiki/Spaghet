# shortcut: règles à la main par mots-clés, se trompent sur les marques multi-produits (Samsung, HP, TP-Link) ; passer à un vrai fingerprint (DHCP, ports) si trop d'erreurs.

# Services mDNS → type. Plus fiables que le fabricant, donc testés en premier.
SERVICE_KINDS = {
    "_ipp._tcp": "printer",
    "_ipps._tcp": "printer",
    "_printer._tcp": "printer",
    "_pdl-datastream._tcp": "printer",
    "_scanner._tcp": "printer",
    "_apple-mobdev2._tcp": "phone",
    "_googlecast._tcp": "iot",
    "_airplay._tcp": "iot",
    "_raop._tcp": "iot",
    "_spotify-connect._tcp": "iot",
    "_hap._tcp": "iot",
    "_matter._tcp": "iot",
    "_sonos._tcp": "iot",
    "_smb._tcp": "pc",
    "_workstation._tcp": "pc",
    "_ssh._tcp": "pc",
    "_sftp-ssh._tcp": "pc",
    "_rdp._tcp": "pc",
    "_kdeconnect._udp": "pc",
}

# Mot-clé dans le nom du fabricant (minuscules) → type.
VENDOR_KINDS = {
    "printer": ["brother", "epson", "canon", "lexmark", "kyocera", "xerox"],
    "phone": ["samsung", "xiaomi", "oneplus", "huawei", "oppo", "vivo", "motorola", "fairphone"],
    "iot": ["espressif", "tuya", "sonos", "philips", "signify", "amazon", "google", "roku", "shelly", "nest", "ring"],
    "pc": ["intel", "dell", "lenovo", "micro-star", "gigabyte", "asrock", "realtek", "framework"],
    "router": ["ubiquiti", "mikrotik", "netgear", "sagemcom", "arcadyan", "technicolor", "freebox"],
}


def is_random_mac(mac: str | None) -> bool:
    """Bit « administré localement » : MAC aléatoire, typique des téléphones récents."""
    return bool(mac) and int(mac.split(":")[0], 16) & 0x02 != 0


def classify(vendor: str | None, services: list[str], mac: str | None = None) -> str | None:
    for service in services:
        if kind := SERVICE_KINDS.get(service.removesuffix(".local.")):
            return kind
    low = (vendor or "").lower()
    for kind, words in VENDOR_KINDS.items():
        if any(w in low for w in words):
            return kind
    if is_random_mac(mac):
        return "phone"
    return None


# Port bien connu → catégorie de trafic. Les couleurs du graphe suivent ces catégories.
# shortcut: classement par port seul ; seuls les domaines de STREAMING_DOMAINS sortent du « web », étendre la liste au besoin.
_PORTS = {
    "web": [80, 443, 8080, 8443],
    "dns": [53, 853],
    "mail": [25, 110, 143, 465, 587, 993, 995],
    "remote": [22, 23, 3389, 5900],
    "files": [20, 21, 139, 445, 548, 2049],
    "streaming": [554, 1935, 7000, 8009, 8554, 32400],
    "p2p": [*range(6881, 6890), 51413],
    "infra": [67, 68, 123, 138, 1900],
}
PORT_PROTOCOLS = {port: proto for proto, ports in _PORTS.items() for port in ports}


def protocol(sport: int | None, dport: int | None, icmp: bool = False) -> str:
    """Catégorie d'un paquet d'après ses ports ; le port de destination est essayé en premier."""
    if icmp:
        return "infra"
    for port in (dport, sport):
        if port in PORT_PROTOCOLS:
            return PORT_PROTOCOLS[port]
    return "other"


# Domaines (regroupés) dont le trafic est de la vidéo ou de l'audio, même s'il passe par le port 443.
STREAMING_DOMAINS = {
    "googlevideo.com",  # YouTube
    "nflxvideo.net",    # Netflix
    "ttvnw.net",        # Twitch
    "aiv-cdn.net",      # Prime Video
    "dssott.com",       # Disney+
    "scdn.co",          # Spotify
    "dzcdn.net",        # Deezer
    "tiktokcdn.com",
}

# shortcut: approximation de la Public Suffix List, se trompe sur les suffixes rares ; passer à tldextract si les regroupements sont faux.
_SECOND_LEVEL = {"co", "com", "net", "org", "gov", "ac", "edu", "ne", "or", "gouv"}


def registrable_domain(host: str) -> str:
    """Domaine « achetable » : www.youtube.com → youtube.com, news.bbc.co.uk → bbc.co.uk."""
    labels = host.lower().rstrip(".").split(".")
    n = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 2
    return ".".join(labels[-n:])
