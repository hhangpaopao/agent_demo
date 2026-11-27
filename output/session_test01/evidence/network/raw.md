## 检索调用记录（共 5 次，达上限）
1) "2025 全球头戴式降噪耳机 市场份额 出货量 IDC Counterpoint 索尼 Bose 森海塞尔 排名" → 失败：搜索服务异常/限流（ConnectionResetError 10054）
2) "IDC 2024 全球头戴式耳机市场 出货量 份额 苹果 索尼 Bose 排名" → 失败：Max retries exceeded / SSLEOFError
3) "WH-1000XM5 vs QuietComfort Ultra vs Momentum 4 review noise cancelling comparison" → 失败：同上 SSL 异常
4) "索尼 WH-1000XM5 头戴式降噪耳机 京东 价格 销量"（include_images=True）→ 成功，8 条结果 + 2 张图
5) "Bose QC消噪耳机Ultra 头戴式 商品主图 价格 销量 对比 森海塞尔MOMENTUM 4 大馒头4 降噪 评测"（include_images=True）→ 成功，7 条结果 + 2 张图

## 主图解析调用记录（2 轮，4 张）
- https://img12.360buyimg.com/n1/jfs/t1/146808/16/27291/56467/627897dbE57495d32/c1e27109b41e1d8c.jpg（标 索尼 WH-1000XM5）→ 读出 1 条：「SONY（上左）」
- https://img12.360buyimg.com/n1/jfs/t1/397954/5/4251/67974/699e97b7F4755696b/008332032045b63b.jpg → 读出 3 条：「无声 更有声（上中）」「WH-1000XM6（上中）」「索尼双芯超旗舰头戴降噪耳机（上中）」
- https://cdn-general.cybassets.com/media/...c68900ec1bbbabb4.jpeg（标 Bose QC Ultra）→ 解析失败 BadRequestError: Missing Content-Length of multimodal url
- https://shop.theclub.com.hk/media/catalog/product/cache/.../b/o/bose_ultraeb2_all_1.jpg → 读出 1 条：「BOSE（中右）」（白底产品图，无卖点文案）

## 逐条网页证据（标题 / URL / 原文关键句）

### A. 知乎·雪夜阳光《怎样购买索尼头戴式降噪耳机WH-1000XM5价格最低？》 https://www.zhihu.com/tardis/bd/art/530301798（编辑于 2024-06-23）
- 「国行版WH-1000XM5在2022年5月19日上市，定价2999元。」
- 「2022年双11优惠活动，京东和天猫国行版WH-1000XM5的价格都降到了2500元以内。」
- 「目前天猫索尼官方旗舰店中，WH-1000XM5限时活动价2489元，如果你是88VIP会员，还可以使用满1500减120的消费券，到手价不高于2379元。」
- 「SONY京东自营官方旗舰店里国行WH-1000XM5双11活动价为2488元。」
- 「目前海外版WH-1000XM5日常售价2299元，双11秒杀价2199元，领券再减300元，最终含税到手价约 2100元，比国行版低约300元。」
- 海外版差异：说明书语言/提示音语言/售后渠道（国行全国联保 vs 海外版由京东负责售后）/不支持7天无理由/无发票；保修均一年。

### B. 京东机型聚合页 https://www.jd.com/zuozhe/67288504d13cc925f72a.html
「索尼（SONY）WH-1000XM5 头戴式无线降噪 蓝牙耳机 AI智能降噪 玫瑰灰 开学必备 适用苹果 200000+ 条评论 自营」
「索尼（SONY）WH-1000XM5 头戴式无线降噪耳机 AI智能降噪 铂金银 开学必备 适用苹果 200000+ 条评论 自营」
「博士（BOSE）QC消噪耳机Ultra-沙丘灰 头戴式降噪无线蓝牙运动智能耳机 主动降噪 适用华为苹果耳机 生日礼物 100000+ 条评论 自营」
「森海塞尔（Sennheiser）【宋威龙同款】MOMENTUM 4 无线耳机 大馒头4蓝牙头戴曜金黑 适用苹果华为 中秋礼物 100000+ 条评论 自营」
（另含 XM5 第三方链接 50000+/10000+ 条、二手 XM5 链接 5000+ 条；页面有"销量""评论数"排序项，无价格回传）

### C. 京东价格行情页 https://www.jd.com/jiage/652b6cad2cdb20f4233.html
「索尼（SONY）WH-1000XM6 头戴式无线降噪耳机 AI智能降噪 铂金银开学必备 适用苹果 50000+ 条评论 自营」
「索尼（SONY）WH-1000XM6 头戴式无线降噪耳机 AI智能降噪 暮砂粉 礼物开学必备 适用苹果 50000+ 条评论 自营」
「索尼（SONY）WH-1000XM5 头戴式无线降噪耳机 AI智能降噪 铂金银 开学必备 适用苹果 200000+ 条评论 自营」
（另在售：WH-CH720N/WH-CH520 各 100000+ 评论、INZONE H3、MDR-7506、MDR-M1、WH-CH730N、INZONE H9 II）

### D. Amazon 类目页 https://www.amazon.com/clp/B09XS7JWHH（含 "Black Friday Deal"、XM6 在售，快照推断 2025 黑五）
- 「Sony WH-1000XM5 Premium Noise Cancelling Wireless Headphones … Mic, Up to 30 Hours of Play time, Black 4.2 out of 5 stars 3,265 3K+ bought in past month -20% $359.00 List Price: $449.00」
- 「Sony Noise Canceling Wireless Headphones - 30hr Battery Life - Over-Ear Style - Optimized for Alexa and Google Assistant - Built-in mic for Calls - WH-1000XM5B.CE7 - Limited Edition - Charcoal Black 4.3 out of 5 stars 8,121 50+ bought in past month $276.99」
- 「Sony WH-1000XM6 The Best Noise Cancelling Wireless Headphones, Black | HD NC Processor QN3, 12 Microphones, Adaptive NC Optimizer, Mastered by Engineers, Studio-Quality, Up to 30 Hrs of Battery 4.1 out of 5 stars 4,088 5K+ bought in past month Deal selling fast -24% $348.00 List Price: $459.99」
- 「Sennheiser MOMENTUM 4 Wireless Headphones, ANC, 60 Hr Battery, Black | Sennheiser Signature Sound, Adaptive Noise Cancellation Plus Transparency Mode, Up to 60-Hour Battery with Fast Charge 4.0 out of 5 stars 14,463 -37% $188.00 List Price: $299.99」
- 「Sony 1000X THE COLLEXION Wireless Noise Cancelling Headphones, Black | Luxury Comfort with Curated Materials, HD NC Processor QN3, 12 Microphones, Mastered by Engineers, Up to 24 Hrs of Battery 3.9 out of 5 stars 169 -16% $548.00 List Price: $649.99」
- 「Bowers & Wilkins Px8 Over-Ear Wireless Headphones, Dark Forest | Limited Edition, Advanced Active Noise Cancellation, Luxurious Materials, 30-Hour Battery Life, 15-Min Quick Charging 4.2 out of 5 stars 2,376 Black Friday Deal -40% $449.00 List Price: $749.00」
- 「Sony WH-1000XM4 Wireless Noise Canceling Overhead Headphones - Black (Renewed) 4.5 out of 5 stars 2,736 -13% $174.99 New Price: $199.99」

### E. 京东降噪耳机排行榜 https://www.jd.com/phb/key_652a2e032a0c2a5ae1b.html
- 「TOP 6 Apple/苹果 AirPods Max 2 星光色（2026款）搭载H2芯片 头戴式 … MHWL4CH/A ¥ 已有20000人评论 苹果的耳机果然是名不虚传…续航能力也非常的强…非常有质感」
- 「TOP 7 索尼（SONY）WH-1000XM5 头戴式无线耳机蓝牙降噪耳机XM4升级… 铂金银 ¥ 已有50000人评论 看了很久，最后趁着双十一下单，价格会比平时便宜一些，耳机降噪效果不错，也可以开透传模式，充一次电可以用好几天，坐高铁超级实用。」
- 「TOP 9 索尼（SONY）WH-1000XM6 头戴式无线降噪耳机 AI智能降噪 铂金银开学必备 ¥ 已有50000人评论 …三频均衡…耳垫柔软，长时间佩戴也不会压头，续航持久，折叠收纳方便」
- 「TOP 5 Viken【2026最新款丨柏林之声第1名】骨传导蓝牙耳机… ¥ 已有200000人评论」（该页存在营销堆词，可信度下调）
- 「TOP 8 Apple AirPods 4(支持主动降噪)… 已有2000000人评论」

### F. SoundGuys《Sony WH-1000XM5 review》 https://www.soundguys.com/sony-wh-1000xm5-review-71783
- 「The predecessors to the Sony WH-1000XM5 were king of the hill, and this new headphone takes that crown. … It excels in travel or at the office, in particular.」
- 规格：Product Release date May 20, 2022；Original price $399；251g；Bluetooth 5.2；Ear-cup 80x98mm
- 评分块：「8.6 SoundGuys Rating / 7.9」（7.9 未注明口径，疑用户分）；「MSRP: $557.95」；「$226.20 at Amazon」
- What we like：ANC performance / Outstanding microphone / App features, including custom EQ / Bluetooth 5.2 with SBC, AAC, LDAC, and wired / Bluetooth multipoint / Find My Device enabled
- What we don't like：Price / No IP rating
- 「when pit against the newer headphones, there's a lot that the WH-1000XM5 lack… and a hinge that's quite a bit more sturdy.」
- Owner review（reader says they own it, 1–2 years）：「uncomfortable headpiece, earpads wear out fast and not too comfy with glasses, not sweatproof, hinges are very dainty and broke」

### G. SoundGuys《Sennheiser MOMENTUM 4 Wireless vs Bose QuietComfort Ultra Headphones》 https://www.soundguys.com/sennheiser-momentum-4-wireless-vs-bose-quietcomfort-ultra-headphones-140166
- 「Bose still wears the ANC crown. The QuietComfort Ultra Headphones reduce environmental noise by an average of 85%, and their seal around the ears helps isolate sound before the ANC even kicks in.」
- 「While the MOMENTUM 4 Wireless performs decently—cutting about 75% of high-pitched noise and low-pitched rumbles—it doesn't quite reach the same attenuation levels.」
- 「According to MDAQS, both headphones scored 4.6 overall… Bose wins slightly on Immersiveness, while Sennheiser holds the edge in accuracy and low distortion.」
- QC Ultra The Bottom Line：「a credible step up from previous Bose ANC headphones… No waterproofing is a bit of a letdown」；Negatives: Some channel matching issues
- MOMENTUM 4：「MSRP: USD299.99 / 8 / Positives: Excellent battery life, Excellent sound quality, Connectivity options, Price / Negatives: Middling ANC, Mic noise rejection」

### H. Recording Now《Sennheiser Momentum 4 vs Bose QuietComfort Ultra in 2025》 https://recordingnow.com/blog/sennheiser-momentum-4-vs-bose-quietcomfort-ultra
- 「Overall Winner: Bose QuietComfort Ultra… the Bose QuietComfort edges out the Sennheiser Momentum 4 on account of its superior comfort, better active noise cancelling performance, and slightly more audiophile sound quality (after EQ).」
- 「Sennheiser Momentum 4 Value: 4.5 out of 5 / Bose QuietComfort Ultra Value: 4.0 out of 5」
- 「Sennheiser Momentum 4 Sound Quality: 8.7 out of 10 (after EQ) / Bose QuietComfort Ultra Sound Quality: 8.9 out of 10 (after EQ)」
- 「I can find the Ultra for around $349 brand new these days… For the Momentum 4, I can find them for just under $300」
- M4 pros：Excellent sound quality / Good noise cancelling / Impressive bass / Amazing 60 hour battery life / Relatively good value；cons：Sony/Bose have better noise cancelling / Headband not as comfortable
- QC Ultra pros：Audiophile-level sound quality (after EQ) / Unrivaled noise cancelling / Insanely comfortable and lightweight；cons：Expensive / Sound signature without EQ is disappointing / Materials feel mediocre for price

### I. 同内容 YouTube https://www.youtube.com/watch?v=uuAJz9zg7LM
- 「the Bose Quiet Comfort Ultra receives a 9.2 out of 10, winning the comparison」/「the Sennheiser Momentum 4 receives an 8.7 out of 10」
- 「the QC Ultra retails at $429 USD, but you can find it on the market today for around $349 brand new… the Momentum 4… under $300」
- 「both of these headphones score much better here than the Apple AirPods Max if we're looking at this $549 retail price」

### J. What Hi-Fi《Bose vs Sennheiser headphones: which should you choose?》 https://www.whathifi.com/advice/bose-vs-sennheiser-headphones-which-should-you-buy
- 「The QuietComfort Ultra Headphones are the pricier pair, even though they have come down significantly from their launch price, and come with a cutting-edge spatial audio feature we think is rather hit-and-miss.」
- 「The Sennheiser Momentum 4 Wireless, meanwhile, thrash them for battery life, packing an astonishing 60 hours to Bose's 24 hours.」
- 「Bose's ANC is, quite simply, the best in the business. … The Bose QuietComfort Ultra Headphones dramatically reduce whatever's making noise… especially effective with low-end rumbles like the London Underground. The Sennheiser Momentum 4 Wireless, while also impressive, can't quite match it.」
- 「The Bose QuietComfort Ultra Headphones are upfront and bold, whereas the Sennheiser Momentum 4 Wireless deliver a more even-handed tonality.」

### K. 知乎/ZOL《森海塞尔MOMENTUM 4评测：令人耳目一新的大升级》 https://zhuanlan.zhihu.com/p/557586002
- 「新一代MOMENTUM 4的首发价2699元，比上一代还便宜了300元」
- 「它的降噪深度与Bose、索尼的现役旗舰头戴耳机属于同一水准，但耳压感要明显好于Bose QC45…MOMENTUM 4和索尼 WH-1000XM5都没有这样的问题，降噪体验非常自然」
- 「在开启降噪的情况下续航可以达到60小时，是隔壁索尼和Bose家现役旗舰头戴降噪耳机的一倍还多…充电5分钟就可以多听4小时音乐」
- 「这一次MOMENTUM 4重要的更新还有全新的触控操作、ANC自适应降噪（新增抗风燥选项）、声音个性化设置」（触控为卖点，非差评证据）
- 缺点：「解析力略显不足，在听一些大编制古典乐时低频会有一点"闷"」；「我并不建议调整太大（味道会变得很奇怪）」；支持 aptX Adaptive（高通协议，功耗高于 SBC/AAC）

### L. 知乎《Bose QC消噪耳机Ultra、QC消噪耳机测评》 https://zhuanlan.zhihu.com/p/661453069
- 「参考售价：3599元」「续航：24小时（降噪开），充电时间3小时，充电15分钟使用2小时」「蓝牙支持：SBC/AAC」
- 「Ultra系列产品的技术亮点是搭载了「主动消噪」与「沉浸式空间音频技术」「「主动消噪」技术结合了CustomTune智能耳内音场调校技术」
- 「头梁采用的新设计使它有了折叠功能（Bose NC700不可折叠）」「耳机腔体是塑料材质，表面是磨砂质感，转轴和伸缩感是金属材质」「（图三左边是SONY WH1000XM5的收纳包）」收纳盒尺寸约 21.1x15.7x6.1 cm
- 「降噪方面依旧是Bose的强项，头戴降噪耳机里面的一流水准」「它是目前综合实力很不错的头戴降噪耳机，除了刚发布价格稍有点高以外几乎没啥毛病」

### M. YouTube《Bose QC Ultra Headphones 2 Vs Sennheiser Momentum 4》 https://www.youtube.com/watch?v=iuwnm1kajE8
- 「if active noise cancellation is very important to you, then you do want to look at either the Sony 1000XM6s or the Sony 1000XM5s」
- 「the Bose QT2s have an advertised battery life of 30 hours with active noise cancellation turned on… the Momentum 4s have 60 hours」
- 「the ambient mode on the Bose does sound better… With the Momentum 4s, it can be a little harder to understand people… it prioritizes noise pollution over people's voices」
- 「the microphone array on the Momentum 4s collects more wind noise when walking outdoors than the Bose」
- 「this USBC port as they should… an upgrade for the Bose QC Ultra 2OS, but both still have an audio jack」

### N. IT之家《1499 元再降价：索尼 WF-1000XM5 降噪豆 12 期免息大促》 https://www.ithome.com/0/729/114.htm（2023 双11，含广告声明）
- 「索尼 WF-1000XM5 真无线降噪耳机 国行版 于今年 7 月正式发售，官方上市价为 1999 元…仅需 1499 元…支持 12 期白条免息，注册再送半年延保」「下单立减 540 元」「领 300 元券」「满 200-20 元补贴券」「秀视频晒单返 50 元 E 卡」
- 海外零售价：「$299.99 美元（约 2145 元人民币）/259 英镑/499 澳元」
- 注：此为 TWS 型号（WF-），仅用作促销节奏样本，不可外推到头戴 XM5
