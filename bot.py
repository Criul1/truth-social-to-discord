import os
import sys
import json
import asyncio
import logging
import re
import html
from datetime import datetime, timezone
from time import mktime

import aiohttp
import feedparser
import discord
from discord.ext import tasks, commands

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('TruthSocialBot')

CONFIG_FILE = 'config.json'
STATE_FILE = 'state.json'

def clean_html(raw_html):
    """Remove HTML tags and unescape HTML entities."""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return html.unescape(cleantext).strip()

def load_config():
    if not os.path.exists(CONFIG_FILE):
        logger.error(f"Config file {CONFIG_FILE} not found. Please create it first.")
        sys.exit(1)
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"last_entry_id": None}
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {"last_entry_id": None}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

config = load_config()
TOKEN = config.get('discord_token')
CHANNEL_ID = config.get('channel_id')
FEED_URL = config.get('feed_url', 'https://trumpstruth.org/feed')
CHECK_INTERVAL = config.get('check_interval_seconds', 60)

if not TOKEN or TOKEN == "YOUR_DISCORD_BOT_TOKEN_HERE":
    logger.error("Please set a valid discord_token in config.json")
    sys.exit(1)

if not CHANNEL_ID or CHANNEL_ID == 123456789012345678:
    logger.error("Please set a valid channel_id in config.json")
    sys.exit(1)

# Initialize bot
intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)

async def fetch_feed():
    try:
        # Prevent caching
        headers = {'Cache-Control': 'no-cache', 'Pragma': 'no-cache'}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(FEED_URL, timeout=15) as response:
                response.raise_for_status()
                content = await response.text()
                # Parse feed
                feed = feedparser.parse(content)
                return feed.entries
    except Exception as e:
        logger.error(f"Error fetching or parsing feed: {e}")
        return None

@tasks.loop(seconds=CHECK_INTERVAL)
async def check_new_posts():
    logger.info("Checking for new posts...")
    entries = await fetch_feed()
    if entries is None:
        return # Error already logged
    
    if not entries:
        logger.info("No entries found in feed.")
        return
        
    state = load_state()
    last_entry_id = state.get('last_entry_id')
    
    # Handle first run
    if last_entry_id is None:
        latest_id = entries[0].get('id', entries[0].get('guid', entries[0].get('link')))
        logger.info(f"First run detected. Storing the latest post ID ({latest_id}) and skipping posting to prevent spam.")
        state['last_entry_id'] = latest_id
        save_state(state)
        return

    new_entries = []
    # Identify new posts by stopping at the last known post ID
    for entry in entries:
        entry_id = entry.get('id', entry.get('guid', entry.get('link')))
        if entry_id == last_entry_id:
            break
        new_entries.append(entry)
        
    if not new_entries:
        logger.info("No new posts found.")
        return
        
    # Reverse to post oldest first (chronological order)
    new_entries.reverse()
    
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        try:
            channel = await bot.fetch_channel(CHANNEL_ID)
        except Exception as e:
            logger.error(f"Could not find or access channel {CHANNEL_ID}: {e}")
            return

    for entry in new_entries:
        title = entry.get('title', 'New Post')
        
        # Clean the summary/description to ensure no raw HTML is sent
        raw_description = entry.get('summary', entry.get('description', ''))
        description = clean_html(raw_description)
        link = entry.get('link', '')
        
        # Format description if it is too long for Discord embeds
        if len(description) > 3900:
            description = description[:3900] + f"...\n\n[Read more...]({link})"

        embed = discord.Embed(
            title=title,
            description=description,
            url=link,
            color=0x1DA1F2,
            timestamp=datetime.now(timezone.utc)
        )
        
        # Parse official publishing date if feed provides it
        if 'published_parsed' in entry and entry.published_parsed:
            try:
                embed.timestamp = datetime.fromtimestamp(mktime(entry.published_parsed), tz=timezone.utc)
            except Exception as e:
                logger.warning(f"Could not parse timestamp: {e}")

        # Set author and footer
        embed.set_author(name="Donald J. Trump", url="https://truthsocial.com/@realDonaldTrump", icon_url="https://truthsocial.com/favicon.ico")
        embed.set_footer(text=f"Source: {FEED_URL}", icon_url="https://truthsocial.com/favicon.ico")
        
        try:
            await channel.send(embed=embed)
            logger.info(f"Successfully posted entry: {link}")
        except Exception as e:
            logger.error(f"Failed to post entry to discord: {e}")
            # Do not update state so it will retry on next poll
            return
            
        # Update state after each successful post
        entry_id = entry.get('id', entry.get('guid', entry.get('link')))
        state['last_entry_id'] = entry_id
        save_state(state)
        
        # Small delay between messages
        await asyncio.sleep(2)

@check_new_posts.before_loop
async def before_check():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user.name} ({bot.user.id})")
    if not check_new_posts.is_running():
        check_new_posts.start()

if __name__ == "__main__":
    try:
        # Prevent errors on close for Windows
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("Bot manually stopped.")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
