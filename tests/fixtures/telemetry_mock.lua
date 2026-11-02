-- Mock LÖVE + Balatro globals, run the telemetry hook once and print the TSV it would compress.
local captured
love = {draw = function() end,
  graphics = {getWidth=function() return 1280 end, getHeight=function() return 720 end,
    push=function() end, pop=function() end, origin=function() end, setCanvas=function() end,
    setShader=function() end, setColor=function() end, rectangle=function() end},
  timer = {getTime=function() return 1 end},
  data = {compress=function(_, _, s) captured = s; return s end}}
local function card(v, s, enh, seal, ed)
  return {base={value=v, suit=s}, seal=seal, edition=ed and {type=ed} or nil, ability={},
          config={center= enh and {set="Enhanced", name=enh} or {set="Default"}},
          T={x=1,y=1,w=1,h=1}, container={T={x=0,y=0}}}
end
local j = {ability={name="Yorick", x_mult=2, yorick_discards=11, rental=true, perishable=true, perish_tally=3, extra={}},
           T={x=1,y=1,w=1,h=1}, container={T={x=0,y=0}}, sell_cost=5}
G = {STAGE=1, STAGES={RUN=1}, STATE=1, STATES={SELECTING_HAND=1, SHOP=2}, TILESCALE=1, TILESIZE=20,
  GAME={blind={name="The Ox", chips=900}, chips=0, current_round={hands_left=4, discards_left=3, discards_used=2, most_played_poker_hand="Pair"},
        dollars=12, stake=8, pseudorandom={seed="TESTSEED"}, round_resets={hands=6, discards=4, ante=3, blind_choices={Boss="bl_ox"}, blind_tags={Small="tag_rare", Big="tag_d6"}},
        round=7, last_tarot_planet="c_jupiter", hands={}},
  P_CENTERS={c_jupiter={name="Jupiter"}}, P_BLINDS={bl_ox={name="The Ox"}},
  P_TAGS={tag_rare={name="Rare Tag"}, tag_d6={name="D6 Tag"}},
  hand={cards={card("King","Spades"), card("10","Hearts","Glass Card","Red","polychrome")}, config={card_limit=8}},
  jokers={cards={j}, config={card_limit=5}}, consumeables={cards={}},
  deck={cards={card("Ace","Clubs"), card("2","Diamonds","Stone Card")}},
}
if arg[2] == "cashout" then
  G.STATES.ROUND_EVAL = 3
  G.STATE = 3
  G.round_eval = {}
  G.I = {UIBOX={{get_UIE_by_ID=function(_, id) return id == "cash_out_button" and {config={button="cash_out"}} end}}}
end
G.playing_cards = {G.hand.cards[1], G.hand.cards[2], G.deck.cards[1], G.deck.cards[2]}
dofile(arg[1])
love.draw()
io.write(captured)
