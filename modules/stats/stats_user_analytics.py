"""
IRC Statistics - User Analytics Module
=======================================
Advanced per-user analytics including:
- Activity patterns (best hours, days)
- Conversation analysis (who talks to whom)
- Sentiment analysis (positive/negative/neutral)
- Behavioral insights

Usage:
    from modules.stats.stats_user_analytics import UserAnalytics
    
    analytics = UserAnalytics(sql_instance)
    
    # Get user's peak hours
    peak_hours = analytics.get_user_peak_hours(bot_id, channel, nick)
    
    # Sentiment analysis
    sentiment = analytics.get_user_sentiment(bot_id, channel, nick)
    
    # Conversation partners
    partners = analytics.get_conversation_partners(bot_id, channel, nick)
"""

import re
from typing import Dict, List, Tuple, Optional, Any
from collections import defaultdict
from datetime import datetime, timedelta
from core.log import get_logger

logger = get_logger("user_analytics")


# =============================================================================
# Sentiment Analysis Patterns
# =============================================================================

class SentimentPatterns:
    """Regex patterns pentru sentiment analysis"""
    
    def __init__(self):
        # Positive patterns
        self.positive_patterns = [
            # Emotions positive
            re.compile(r'\b(awesome|amazing|great|good|nice|cool|perfect|excellent|fantastic|wonderful|brilliant)\b', re.I),
            re.compile(r'\b(love|like|enjoy|happy|glad|thanks|thank you|appreciate)\b', re.I),
            re.compile(r'\b(yes|yeah|yep|sure|absolutely|definitely|agreed)\b', re.I),
            
            # Emoticons positive
            re.compile(r'(:\)|:-\)|=\)|:D|:-D|=D|\^_\^|:3)'),
            re.compile(r'(😊|😃|😄|🙂|😁|😆|🥰|❤️|♥️|<3)'),
            re.compile(r'\b(haha|hehe|lol|lmao|rofl)\b', re.I),
        ]
        
        # Negative patterns
        self.negative_patterns = [
            # Emotions negative
            re.compile(r'\b(bad|terrible|awful|horrible|worst|hate|suck|fail|failed|error)\b', re.I),
            re.compile(r'\b(sad|angry|mad|annoyed|frustrated|upset|disappointed)\b', re.I),
            re.compile(r'\b(no|nope|nah|never|cant|cannot|dont|wont)\b', re.I),
            re.compile(r'\b(stupid|idiot|dumb|crap|shit|damn|wtf|fuck)\b', re.I),
            
            # Emoticons negative
            re.compile(r'(:\(|:-\(|=\()'),
            re.compile(r'(😢|😭|😠|😡|☹️|🙁)'),
        ]
        
        # Neutral/question patterns
        self.question_patterns = [
            re.compile(r'\?'),
            re.compile(r'\b(what|when|where|who|why|how|which)\b', re.I),
        ]
    
    def analyze(self, text: str) -> Dict[str, Any]:
        """
        Analyze sentiment of text.
        
        Returns:
            dict: {
                'sentiment': 'positive'|'negative'|'neutral',
                'score': float (-1.0 to 1.0),
                'positive_count': int,
                'negative_count': int,
                'is_question': bool
            }
        """
        if not text:
            return {
                'sentiment': 'neutral',
                'score': 0.0,
                'positive_count': 0,
                'negative_count': 0,
                'is_question': False
            }
        
        # Count matches
        positive_count = sum(
            len(pattern.findall(text))
            for pattern in self.positive_patterns
        )
        
        negative_count = sum(
            len(pattern.findall(text))
            for pattern in self.negative_patterns
        )
        
        is_question = any(
            pattern.search(text)
            for pattern in self.question_patterns
        )
        
        # Calculate sentiment
        total = positive_count + negative_count
        
        if total == 0:
            sentiment = 'neutral'
            score = 0.0
        else:
            score = (positive_count - negative_count) / total
            
            if score > 0.2:
                sentiment = 'positive'
            elif score < -0.2:
                sentiment = 'negative'
            else:
                sentiment = 'neutral'
        
        return {
            'sentiment': sentiment,
            'score': score,
            'positive_count': positive_count,
            'negative_count': negative_count,
            'is_question': is_question
        }


# =============================================================================
# User Analytics Main Class
# =============================================================================

class UserAnalytics:
    """
    Advanced user analytics and behavioral insights.
    """
    
    def __init__(self, sql_instance):
        self.sql = sql_instance
        self.sentiment = SentimentPatterns()
        logger.info("UserAnalytics initialized")
    
    # =========================================================================
    # Activity Patterns
    # =========================================================================
    
    def get_user_peak_hours(
        self,
        bot_id: int,
        channel: str,
        nick: str,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Get user's most active hours of the day.
        
        Returns:
            List of {hour, messages, percentage} sorted by activity
        """
        
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        query = """
            SELECT 
                CAST(strftime('%H', datetime(ts, 'unixepoch')) AS INTEGER) as hour,
                COUNT(*) as messages
            FROM IRC_EVENTS
            WHERE botId = ? 
              AND channel = ? 
              AND nick = ?
              AND event_type IN ('PRIVMSG', 'ACTION')
              AND date(ts, 'unixepoch') >= ?
            GROUP BY hour
            ORDER BY messages DESC
        """
        
        rows = self.sql.sqlite_select(query, (bot_id, channel, nick, cutoff_date))
        
        if not rows:
            return []
        
        total_messages = sum(row[1] for row in rows)
        
        results = []
        for hour, messages in rows:
            results.append({
                'hour': hour,
                'messages': messages,
                'percentage': (messages / total_messages * 100) if total_messages > 0 else 0,
                'time_label': f"{hour:02d}:00-{hour:02d}:59"
            })
        
        return results
    
    def get_user_peak_days(
        self,
        bot_id: int,
        channel: str,
        nick: str,
        weeks: int = 4
    ) -> List[Dict[str, Any]]:
        """
        Get user's most active days of week.
        
        Returns:
            List of {day_of_week, day_name, messages, percentage}
        """
        
        cutoff_date = (datetime.now() - timedelta(weeks=weeks * 7)).strftime("%Y-%m-%d")
        
        query = """
            SELECT 
                CAST(strftime('%w', datetime(ts, 'unixepoch')) AS INTEGER) as dow,
                COUNT(*) as messages
            FROM IRC_EVENTS
            WHERE botId = ? 
              AND channel = ? 
              AND nick = ?
              AND event_type IN ('PRIVMSG', 'ACTION')
              AND date(ts, 'unixepoch') >= ?
            GROUP BY dow
            ORDER BY messages DESC
        """
        
        rows = self.sql.sqlite_select(query, (bot_id, channel, nick, cutoff_date))
        
        if not rows:
            return []
        
        day_names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
        total_messages = sum(row[1] for row in rows)
        
        results = []
        for dow, messages in rows:
            results.append({
                'day_of_week': dow,
                'day_name': day_names[dow],
                'messages': messages,
                'percentage': (messages / total_messages * 100) if total_messages > 0 else 0
            })
        
        return results
    
    def get_user_activity_heatmap(
        self,
        bot_id: int,
        channel: str,
        nick: str,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Get complete activity heatmap (hour x day_of_week).
        
        Returns:
            dict: {
                'heatmap': [[hour0_sun, hour0_mon, ...], [hour1_sun, ...], ...],
                'total_messages': int,
                'peak_hour': int,
                'peak_day': int
            }
        """
        
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        query = """
            SELECT 
                CAST(strftime('%H', datetime(ts, 'unixepoch')) AS INTEGER) as hour,
                CAST(strftime('%w', datetime(ts, 'unixepoch')) AS INTEGER) as dow,
                COUNT(*) as messages
            FROM IRC_EVENTS
            WHERE botId = ? 
              AND channel = ? 
              AND nick = ?
              AND event_type IN ('PRIVMSG', 'ACTION')
              AND date(ts, 'unixepoch') >= ?
            GROUP BY hour, dow
        """
        
        rows = self.sql.sqlite_select(query, (bot_id, channel, nick, cutoff_date))
        
        # Initialize 24x7 matrix
        heatmap = [[0 for _ in range(7)] for _ in range(24)]
        total = 0
        max_val = 0
        peak_hour = 0
        peak_day = 0
        
        for hour, dow, messages in rows:
            heatmap[hour][dow] = messages
            total += messages
            
            if messages > max_val:
                max_val = messages
                peak_hour = hour
                peak_day = dow
        
        return {
            'heatmap': heatmap,
            'total_messages': total,
            'peak_hour': peak_hour,
            'peak_day': peak_day,
            'peak_messages': max_val
        }
    
    # =========================================================================
    # Conversation Analysis
    # =========================================================================
    
    def get_conversation_partners(
        self,
        bot_id: int,
        channel: str,
        nick: str,
        days: int = 30,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get who this user talks to most (based on reply pairs).
        
        Returns:
            List of {
                'partner': str,
                'replies_to': int,      # times user replied to partner
                'replies_from': int,    # times partner replied to user
                'total_interactions': int,
                'interaction_score': float
            }
        """
        
        # Get replies TO this user (others -> user)
        query_to = """
            SELECT to_nick as partner, SUM(count) as replies
            FROM STATS_REPLY_PAIRS
            WHERE botId = ? 
              AND channel = ? 
              AND from_nick = ?
            GROUP BY to_nick
        """
        
        # Get replies FROM this user (user -> others)
        query_from = """
            SELECT from_nick as partner, SUM(count) as replies
            FROM STATS_REPLY_PAIRS
            WHERE botId = ? 
              AND channel = ? 
              AND to_nick = ?
            GROUP BY from_nick
        """
        
        replies_to = {}
        for row in self.sql.sqlite_select(query_to, (bot_id, channel, nick)):
            replies_to[row[0]] = row[1]
        
        replies_from = {}
        for row in self.sql.sqlite_select(query_from, (bot_id, channel, nick)):
            replies_from[row[0]] = row[1]
        
        # Merge results
        all_partners = set(replies_to.keys()) | set(replies_from.keys())
        
        results = []
        for partner in all_partners:
            to_count = replies_to.get(partner, 0)
            from_count = replies_from.get(partner, 0)
            total = to_count + from_count
            
            # Interaction score: bidirectional is better
            # Score = total * (1 + bidirectional_bonus)
            bidirectional_bonus = min(to_count, from_count) / max(to_count, from_count, 1) * 0.5
            score = total * (1 + bidirectional_bonus)
            
            results.append({
                'partner': partner,
                'replies_to': to_count,
                'replies_from': from_count,
                'total_interactions': total,
                'interaction_score': score
            })
        
        # Sort by interaction score
        results.sort(key=lambda x: x['interaction_score'], reverse=True)
        
        return results[:limit]
    
    def get_conversation_graph(
        self,
        bot_id: int,
        channel: str,
        min_interactions: int = 5
    ) -> Dict[str, Any]:
        """
        Get complete conversation graph for channel.
        
        Returns:
            dict: {
                'nodes': [{'nick': str, 'total_messages': int}, ...],
                'edges': [{'from': str, 'to': str, 'weight': int}, ...]
            }
        """
        
        # Get all reply pairs
        query = """
            SELECT from_nick, to_nick, count
            FROM STATS_REPLY_PAIRS
            WHERE botId = ? 
              AND channel = ?
              AND count >= ?
            ORDER BY count DESC
        """
        
        rows = self.sql.sqlite_select(query, (bot_id, channel, min_interactions))
        
        # Build graph
        nodes = {}  # nick -> message count
        edges = []
        
        for from_nick, to_nick, count in rows:
            # Track nodes
            nodes[from_nick] = nodes.get(from_nick, 0) + count
            nodes[to_nick] = nodes.get(to_nick, 0) + count
            
            # Add edge
            edges.append({
                'from': from_nick,
                'to': to_nick,
                'weight': count
            })
        
        # Convert nodes to list
        node_list = [
            {'nick': nick, 'total_messages': count}
            for nick, count in nodes.items()
        ]
        
        return {
            'nodes': node_list,
            'edges': edges,
            'total_nodes': len(node_list),
            'total_edges': len(edges)
        }
    
    # =========================================================================
    # Sentiment Analysis
    # =========================================================================
    
    def get_user_sentiment(
        self,
        bot_id: int,
        channel: str,
        nick: str,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Analyze user's overall sentiment based on messages.
        
        Returns:
            dict: {
                'overall_sentiment': 'positive'|'negative'|'neutral',
                'average_score': float,
                'positive_messages': int,
                'negative_messages': int,
                'neutral_messages': int,
                'total_analyzed': int,
                'sentiment_trend': List[{date, score}]
            }
        """
        
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        query = """
            SELECT message, date(ts, 'unixepoch') as day
            FROM IRC_EVENTS
            WHERE botId = ? 
              AND channel = ? 
              AND nick = ?
              AND event_type IN ('PRIVMSG', 'ACTION')
              AND message IS NOT NULL
              AND date(ts, 'unixepoch') >= ?
            ORDER BY ts DESC
            LIMIT 1000
        """
        
        rows = self.sql.sqlite_select(query, (bot_id, channel, nick, cutoff_date))
        
        if not rows:
            return {
                'overall_sentiment': 'neutral',
                'average_score': 0.0,
                'positive_messages': 0,
                'negative_messages': 0,
                'neutral_messages': 0,
                'total_analyzed': 0,
                'sentiment_trend': []
            }
        
        # Analyze each message
        sentiment_counts = {'positive': 0, 'negative': 0, 'neutral': 0}
        total_score = 0.0
        daily_scores = defaultdict(list)
        
        for message, day in rows:
            result = self.sentiment.analyze(message)
            
            sentiment_counts[result['sentiment']] += 1
            total_score += result['score']
            daily_scores[day].append(result['score'])
        
        total = len(rows)
        avg_score = total_score / total if total > 0 else 0.0
        
        # Determine overall sentiment
        if avg_score > 0.1:
            overall = 'positive'
        elif avg_score < -0.1:
            overall = 'negative'
        else:
            overall = 'neutral'
        
        # Calculate trend
        trend = []
        for day in sorted(daily_scores.keys()):
            day_avg = sum(daily_scores[day]) / len(daily_scores[day])
            trend.append({
                'date': day,
                'score': day_avg,
                'messages': len(daily_scores[day])
            })
        
        return {
            'overall_sentiment': overall,
            'average_score': avg_score,
            'positive_messages': sentiment_counts['positive'],
            'negative_messages': sentiment_counts['negative'],
            'neutral_messages': sentiment_counts['neutral'],
            'total_analyzed': total,
            'sentiment_trend': trend
        }
    
    def get_sentiment_by_hour(
        self,
        bot_id: int,
        channel: str,
        nick: str,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Analyze sentiment variation by hour of day.
        Shows if user is happier at certain times.
        
        Returns:
            List of {hour, avg_score, sentiment, messages}
        """
        
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        query = """
            SELECT 
                CAST(strftime('%H', datetime(ts, 'unixepoch')) AS INTEGER) as hour,
                message
            FROM IRC_EVENTS
            WHERE botId = ? 
              AND channel = ? 
              AND nick = ?
              AND event_type IN ('PRIVMSG', 'ACTION')
              AND message IS NOT NULL
              AND date(ts, 'unixepoch') >= ?
            ORDER BY ts DESC
            LIMIT 5000
        """
        
        rows = self.sql.sqlite_select(query, (bot_id, channel, nick, cutoff_date))
        
        # Group by hour
        hourly_scores = defaultdict(list)
        
        for hour, message in rows:
            result = self.sentiment.analyze(message)
            hourly_scores[hour].append(result['score'])
        
        # Calculate averages
        results = []
        for hour in range(24):
            if hour in hourly_scores:
                scores = hourly_scores[hour]
                avg_score = sum(scores) / len(scores)
                
                if avg_score > 0.1:
                    sentiment = 'positive'
                elif avg_score < -0.1:
                    sentiment = 'negative'
                else:
                    sentiment = 'neutral'
                
                results.append({
                    'hour': hour,
                    'avg_score': avg_score,
                    'sentiment': sentiment,
                    'messages': len(scores),
                    'time_label': f"{hour:02d}:00"
                })
        
        # Sort by hour
        results.sort(key=lambda x: x['hour'])
        
        return results
    
    # =========================================================================
    # Behavioral Insights
    # =========================================================================
    
    def get_user_profile(
        self,
        bot_id: int,
        channel: str,
        nick: str,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Complete user behavioral profile.
        
        Returns comprehensive analysis combining all metrics.
        """
        
        # Get basic stats from STATS_DAILY
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        stats_query = """
            SELECT 
                SUM(messages + actions) as total_messages,
                SUM(words) as total_words,
                SUM(questions) as total_questions,
                SUM(urls) as total_urls,
                SUM(smiles + laughs + hearts) as positive_emotes,
                SUM(sads + angries) as negative_emotes,
                MIN(first_seen_ts) as first_seen,
                MAX(last_seen_ts) as last_seen
            FROM STATS_DAILY
            WHERE botId = ? 
              AND channel = ? 
              AND nick = ?
              AND date >= ?
        """
        
        stats = self.sql.sqlite_select(stats_query, (bot_id, channel, nick, cutoff_date))
        
        if not stats or not stats[0][0]:
            return {
                'error': 'No data found for user',
                'nick': nick,
                'channel': channel,
                'days_analyzed': days
            }
        
        total_msgs, total_words, questions, urls, pos_emotes, neg_emotes, first_seen, last_seen = stats[0]
        
        # Calculate derived metrics
        avg_words_per_msg = (total_words / total_msgs) if total_msgs > 0 else 0
        question_rate = (questions / total_msgs * 100) if total_msgs > 0 else 0
        url_rate = (urls / total_msgs * 100) if total_msgs > 0 else 0
        
        # Get other analytics
        peak_hours = self.get_user_peak_hours(bot_id, channel, nick, days)
        peak_days = self.get_user_peak_days(bot_id, channel, nick, max(4, days // 7))
        partners = self.get_conversation_partners(bot_id, channel, nick, days, limit=5)
        sentiment = self.get_user_sentiment(bot_id, channel, nick, days)
        
        return {
            'nick': nick,
            'channel': channel,
            'period': {
                'days': days,
                'start_date': cutoff_date,
                'first_seen': first_seen,
                'last_seen': last_seen
            },
            'activity': {
                'total_messages': total_msgs,
                'total_words': total_words,
                'avg_words_per_message': round(avg_words_per_msg, 2),
                'questions': questions,
                'question_rate': round(question_rate, 2),
                'urls_shared': urls,
                'url_rate': round(url_rate, 2)
            },
            'emotions': {
                'positive_emotes': pos_emotes,
                'negative_emotes': neg_emotes,
                'emote_ratio': round((pos_emotes / max(neg_emotes, 1)), 2)
            },
            'patterns': {
                'peak_hours': peak_hours[:3],
                'peak_days': peak_days[:3]
            },
            'social': {
                'top_conversation_partners': partners
            },
            'sentiment': sentiment
        }
    
    def compare_users(
        self,
        bot_id: int,
        channel: str,
        nick1: str,
        nick2: str,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Compare two users side-by-side.
        
        Returns comparative analysis.
        """
        
        profile1 = self.get_user_profile(bot_id, channel, nick1, days)
        profile2 = self.get_user_profile(bot_id, channel, nick2, days)
        
        if 'error' in profile1 or 'error' in profile2:
            return {
                'error': 'Insufficient data for comparison',
                'nick1': nick1,
                'nick2': nick2
            }
        
        return {
            'comparison': {
                'nick1': nick1,
                'nick2': nick2,
                'period_days': days
            },
            'metrics': {
                'total_messages': {
                    nick1: profile1['activity']['total_messages'],
                    nick2: profile2['activity']['total_messages'],
                    'winner': nick1 if profile1['activity']['total_messages'] > profile2['activity']['total_messages'] else nick2
                },
                'avg_words': {
                    nick1: profile1['activity']['avg_words_per_message'],
                    nick2: profile2['activity']['avg_words_per_message'],
                    'winner': nick1 if profile1['activity']['avg_words_per_message'] > profile2['activity']['avg_words_per_message'] else nick2
                },
                'sentiment': {
                    nick1: profile1['sentiment']['overall_sentiment'],
                    nick2: profile2['sentiment']['overall_sentiment']
                },
                'question_rate': {
                    nick1: profile1['activity']['question_rate'],
                    nick2: profile2['activity']['question_rate']
                }
            },
            'detailed': {
                nick1: profile1,
                nick2: profile2
            }
        }


# =============================================================================
# Quick Access Functions
# =============================================================================

_analytics_instance = None

def get_analytics(sql_instance) -> UserAnalytics:
    """Get or create UserAnalytics singleton"""
    global _analytics_instance
    
    if _analytics_instance is None:
        _analytics_instance = UserAnalytics(sql_instance)
    
    return _analytics_instance


# =============================================================================
# Testing
# =============================================================================

if __name__ == "__main__":
    print("User Analytics Module")
    print("=" * 70)
    print()
    print("Features:")
    print("  ✅ Activity patterns (peak hours, peak days, heatmap)")
    print("  ✅ Conversation analysis (partners, interaction graph)")
    print("  ✅ Sentiment analysis (positive/negative/neutral)")
    print("  ✅ User profiles (comprehensive behavioral insights)")
    print("  ✅ User comparisons (side-by-side analysis)")
    print()
    print("Ready for integration!")
