import unittest

from hotstar_catalogue_parser import extract_catalogue_routes, extract_live_events, parse_catalogue_page


def navigation(cid="1540083251", slug=None):
    return {"page_navigation": {"page_type": "WatchPage",
        "page_slug": slug or f"/in/sports/hockey/example/{cid}/video/live/watch",
        "page_url": f"/v2/pages/watch?content_id={cid}&filters=content_type%3Dsport_live"}}


def card(cid="1540083251", live=True, title="Example match", slug=None):
    result = {"content_id": cid, "footer": {"title": title},
              "image": {"src": "sources/r1/cms/prod/example-h"},
              "actions": {"on_click": [navigation(cid, slug)]},
              "expanded_content_poster": {"content_info": {
                  "languages": [{"key": "hin", "value": "Hindi"}],
                  "tags": [{"value": "Hindi"}, {"value": "Hockey"}]}}}
    if live:
        result["live_badge"] = {"text_image": {"alt": "LIVE"}}
    return result


def tray(items, title="LIVE Now", next_url=None):
    result = {"items": [{"horizontal_content_card": {"data": item}} for item in items],
              "header": {"regular_tray_header": {"title": title, "actions": {"on_click": [{
                  "page_navigation": {"page_type": "TrayDetailsPage", "page_url":
                    "/v2/pages/tray_details?tray_id=123&tray_title=LIVE+Now&tray_type=editorial"}}]}}}}
    if next_url:
        result["next_tray_url"] = next_url
    return result


class CatalogueTests(unittest.TestCase):
    def test_live_cards_use_canonical_watch_links_and_metadata(self):
        events = extract_live_events(tray([card()]))
        self.assertEqual(len(events), 1)
        item = events[0]
        self.assertEqual(item["id"], "hs:1540083251")
        self.assertEqual(item["provider"], "JioHotstar")
        self.assertTrue(item["isLive"])
        self.assertEqual(item["title"], "Example match")
        self.assertEqual(item["competition"], "Hockey")
        self.assertEqual(item["language"], "Hindi")
        self.assertEqual(item["hotstarUrl"], "https://www.hotstar.com/in/sports/hockey/example/1540083251/video/live/watch")
        self.assertIsNone(item["streamUrl"])
        self.assertTrue(item["poster"].startswith("https://img.hotstar.com/image/upload/"))

    def test_sports_and_live_tray_membership_do_not_make_recordings_or_upcoming_live(self):
        recorded = card("1540083252", live=False, title="Highlights")
        upcoming = card("1540083253", live=False, title="Tomorrow")
        upcoming["status"] = "UPCOMING"
        self.assertEqual(extract_live_events(tray([recorded, upcoming])), [])

    def test_vod_action_overrides_misleading_live_artwork(self):
        episode = card(title="Today's episode", slug="/in/shows/bigg-boss/1971002586/episode/1540083251/watch")
        episode["actions"]["on_click"][0]["page_navigation"]["page_url"] = "/v2/pages/watch?content_id=1540083251&filters=content_type%3Depisode"
        self.assertEqual(extract_live_events(episode), [])
        live = card(slug="/in/shows/channel/1540083251/watch")
        live["actions"]["on_click"][0]["page_navigation"]["page_url"] = "/v2/pages/watch?content_id=1540083251&filters=content_type%3Dshow_live"
        self.assertEqual(len(extract_live_events(live)), 1)

    def test_explicit_masthead_live_info_is_supported_and_duplicate_card_preferred(self):
        masthead = {"content_id": "1540083251", "hero_img": {"src": "sources/r1/hero"},
                    "content_info": {"title": "Masthead title", "live_info": {"live_display_text": "LIVE"},
                                     "primary_cta": {"actions": {"on_click": [navigation()]}}}}
        self.assertEqual(len(extract_live_events({"gec_masthead": {"data": masthead}})), 1)
        events = extract_live_events({"items": [{"gec_masthead": {"data": masthead}},
                                                 {"horizontal_content_card": {"data": card()}}]})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Example match")

    def test_malicious_hosts_ids_credentials_and_missing_watch_links_are_rejected(self):
        for slug in ("https://evil.example/in/sports/x/1540083251/live",
                     "https://www.hotstar.com.evil.example/in/sports/x/1540083251/live",
                     "https://user@www.hotstar.com/in/sports/x/1540083251/live",
                     "/in/sports/x/1540083252/live", "//evil.example/in/sports/x/1540083251/live"):
            self.assertEqual(extract_live_events(card(slug=slug)), [], slug)
        missing = card()
        missing.pop("actions")
        self.assertEqual(extract_live_events(missing), [])

    def test_query_and_fragment_are_removed_from_web_links(self):
        item = card(slug="https://hotstar.com/in/sports/x/1540083251/live?tracking=private#extra")
        self.assertEqual(extract_live_events(item)[0]["hotstarUrl"], "https://www.hotstar.com/in/sports/x/1540083251/live")

    def test_live_view_all_and_pagination_preserve_provider_queries(self):
        next_url = "/v2/pages/tray_details?tray_id=123&offset=10&token=a%2Bb"
        routes = extract_catalogue_routes(tray([card()], next_url=next_url))
        self.assertIn(next_url, routes)
        self.assertIn("/v2/pages/tray_details?tray_id=123&tray_title=LIVE+Now&tray_type=editorial", routes)
        self.assertEqual(extract_catalogue_routes(tray([card(live=False)], title="Highlights", next_url=next_url)), [])

    def test_category_navigation_excludes_vod_and_watch_pages(self):
        data = {"items": [{"page_navigation": {"page_type": "LandingPage", "page_url": route}}
                          for route in ("/v2/pages/sports", "/v2/pages/channels", "/v2/pages/movies",
                                        "https://evil.example/v2/pages/sports", "/v2/pages/../private")]
                + [navigation()]}
        self.assertEqual(extract_catalogue_routes(data), ["/v2/pages/sports", "/v2/pages/channels"])

    def test_empty_malformed_and_recursive_data_are_bounded(self):
        self.assertEqual(parse_catalogue_page(None), ([], []))
        recursive = {"x": []}
        recursive["x"].append(recursive)
        self.assertEqual(parse_catalogue_page(recursive), ([], []))


if __name__ == "__main__":
    unittest.main()
