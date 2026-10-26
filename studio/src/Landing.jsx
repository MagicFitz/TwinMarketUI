import React, { useState } from "react";
import { Badge, Box, Button, Card, Flex, Grid, Heading, SegmentedControl, Text } from "@radix-ui/themes";
import {
  ArrowRightIcon,
  BarChartIcon,
  CheckIcon,
  CopyIcon,
  GearIcon,
  GitHubLogoIcon,
  GlobeIcon,
  PersonIcon,
  RocketIcon,
} from "@radix-ui/react-icons";
import { icon } from "./ui.jsx";
import { useRouter } from "./router.jsx";
import "./landing.css";
import banner from "./assets/brand/banner.svg";
import overviewFigure from "./assets/landing/TwinMarket.jpg";
import frameworkFigure from "./assets/landing/framework.jpg";
import dataFigure from "./assets/landing/data.jpg";
import rumorFigure from "./assets/landing/vis2.jpg";
import stylizedFigure from "./assets/landing/vis4.jpg";
import indexFigure from "./assets/landing/price_index_plot.jpg";
import cuhkLogo from "./assets/landing/CUHK.svg";
import njuLogo from "./assets/landing/NJU.svg";
import shotList from "./assets/landing/studio-experiment-list.jpg";
import shotProgress from "./assets/landing/studio-run-progress.jpg";
import shotResults from "./assets/landing/studio-run-results.jpg";
import shotAgent from "./assets/landing/studio-agent.jpg";
import shotMarket from "./assets/landing/studio-market.jpg";

// 论文主页内容的 Studio 版本：前半部分介绍研究，后半部分介绍 Research Studio 本身。
const PAPER = {
  arxiv: "https://arxiv.org/abs/2502.01506",
  code: "https://github.com/freedomintelligence/TwinMarket",
  poster: "https://neurips.cc/media/PosterPDFs/NeurIPS%202025/116676.png?t=1762476065.2065222",
  talk: "https://iclr.cc/virtual/2025/10000145",
};

const AUTHORS = [
  ["Yuzhe Yang", "1,*"],
  ["Yifei Zhang", "1,2,*"],
  ["Minghao Wu", "1,2,*"],
  ["Kaidi Zhang", "1"],
  ["Yunmiao Zhang", "2"],
  ["Honghai Yu", "2,‡"],
  ["Yan Hu", "1,‡"],
  ["Benyou Wang", "1,‡"],
];

const STATS = [
  ["1000+", "LLM 投资者 Agent"],
  ["50", "上证 50 成分股"],
  ["4", "复现的典型事实"],
  ["BDI", "信念-愿望-意图框架"],
];

const SECTIONS = [
  ["overview", "概览"],
  ["framework", "框架"],
  ["results", "实验结果"],
  ["studio", "Research Studio"],
  ["citation", "引用"],
];

const SPACES = [
  {
    id: "Experiment",
    icon: RocketIcon,
    name: "实验",
    text: "配置模拟区间、Agent 规模与市场快照，运行、停止或从断点续跑，在工作台里查看进度、Agent、结果与日志。",
  },
  {
    id: "Agent",
    icon: PersonIcon,
    name: "智能体",
    text: "浏览基于真实用户画像构建的投资者群体，查看每个 Agent 的风格、偏差与持仓。",
  },
  {
    id: "Market",
    icon: GlobeIcon,
    name: "市场",
    text: "管理交易机制与行业指数资产包，用 Tushare 同步收盘行情，发布可复现的不可变环境快照。",
  },
  {
    id: "Setting",
    icon: GearIcon,
    name: "设置",
    text: "配置并测试语言模型与向量模型连接，管理数据源凭证与全局偏好。",
  },
];

const WORKFLOW = [
  ["配置", "选择区间、Agent 数量、并发与市场快照，或复制已有实验。"],
  ["运行", "后台按交易日推进，进度以持久化运行事件记录，可随时停止。"],
  ["观察", "实时查看每日行情、Agent 决策、社交帖子与诊断日志。"],
  ["分析", "对比模拟指数与真实行情，导出成交、委托与 Agent 结果。"],
];

const SHOTS = [
  ["list", "实验列表", shotList],
  ["progress", "运行进度", shotProgress],
  ["results", "结果分析", shotResults],
  ["agent", "智能体", shotAgent],
  ["market", "市场", shotMarket],
];

const BIBTEX = `@inproceedings{yang2025twinmarket,
  title={TwinMarket: A Scalable Behavioral and Social Simulation for Financial Markets},
  author={Yuzhe Yang and Yifei Zhang and Minghao Wu and Kaidi Zhang and
          Yunmiao Zhang and Honghai Yu and Yan Hu and Benyou Wang},
  booktitle={The Thirty-ninth Annual Conference on Neural Information Processing Systems (NeurIPS)},
  year={2025},
  url={https://arxiv.org/abs/2502.01506},
}`;

function SectionTitle({ eyebrow, title, children }) {
  return (
    <Box mb="6" className="landing-title">
      <Text as="p" size="1" weight="bold" className="landing-eyebrow">
        {eyebrow}
      </Text>
      <Heading as="h2" size="7" weight="bold">
        {title}
      </Heading>
      {children && (
        <Text as="p" size="3" color="gray" mt="3">
          {children}
        </Text>
      )}
    </Box>
  );
}

function Figure({ src, alt, caption }) {
  return (
    <figure className="landing-figure">
      <img src={src} alt={alt} loading="lazy" />
      {caption && <figcaption>{caption}</figcaption>}
    </figure>
  );
}

function FeatureCard({ title, children }) {
  return (
    <Card size="2" className="landing-card">
      <Heading as="h4" size="3" mb="2">
        {title}
      </Heading>
      <Text as="div" size="2" color="gray">
        {children}
      </Text>
    </Card>
  );
}

function Browser({ src, alt }) {
  return (
    <div className="landing-browser">
      <div className="landing-browser-bar" aria-hidden="true">
        <span />
        <span />
        <span />
        <em>localhost · TwinMarket Research Studio</em>
      </div>
      <img src={src} alt={alt} />
    </div>
  );
}

function Citation() {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(BIBTEX);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="landing-bibtex">
      <Button size="1" variant="soft" color="gray" onClick={copy} className="landing-copy">
        {icon(copied ? CheckIcon : CopyIcon, 12)}
        {copied ? "已复制" : "复制"}
      </Button>
      <pre>
        <code>{BIBTEX}</code>
      </pre>
    </div>
  );
}

export function Landing() {
  const { navigate } = useRouter();
  const [shot, setShot] = useState("results");
  const open = (path) => (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(path);
  };
  const current = SHOTS.find(([id]) => id === shot);

  return (
    <div className="landing">
      <header className="landing-nav">
        <a className="landing-brand" href="/studio/Home" onClick={open("/studio/Home")}>
          <img src={banner} alt="TwinMarketUI" />
        </a>
        <nav aria-label="页面目录">
          {SECTIONS.map(([id, label]) => (
            <a key={id} href={`#${id}`}>
              {label}
            </a>
          ))}
        </nav>
        <Button asChild size="2">
          <a href="/studio/Experiment" onClick={open("/studio/Experiment")}>
            进入 Studio {icon(ArrowRightIcon)}
          </a>
        </Button>
      </header>

      <section className="landing-hero">
        <div className="landing-hero-grid" aria-hidden="true" />
        <div className="landing-wrap landing-hero-inner">
          <Flex gap="2" wrap="wrap" justify="center" mb="5">
            <Badge size="2" variant="surface" radius="full">
              NeurIPS 2025
            </Badge>
            <Badge size="2" variant="surface" color="amber" radius="full">
              🏆 ICLR 2025 Financial AI Best Paper
            </Badge>
          </Flex>
          <h1>
            <span className="landing-gradient">TwinMarket</span>
            <small>A Scalable Behavioral and Social Simulation for Financial Markets</small>
          </h1>
          <Text as="p" size="4" color="gray" className="landing-lede">
            用大语言模型驱动上千名具有真实画像、认知偏差与社交关系的投资者，从个体决策中涌现出泡沫、崩盘与市场典型事实。
            <strong> Research Studio </strong>
            把这套模拟带到浏览器里：配置、运行、观察与分析，一站完成。
          </Text>
          <Flex gap="3" wrap="wrap" justify="center" mt="6">
            <Button asChild size="3">
              <a href="/studio/Experiment" onClick={open("/studio/Experiment")}>
                {icon(RocketIcon)} 打开 Research Studio
              </a>
            </Button>
            <Button asChild size="3" variant="soft" color="gray">
              <a href={PAPER.arxiv} target="_blank" rel="noreferrer">
                arXiv 论文
              </a>
            </Button>
            <Button asChild size="3" variant="soft" color="gray">
              <a href={PAPER.code} target="_blank" rel="noreferrer">
                {icon(GitHubLogoIcon)} 代码
              </a>
            </Button>
          </Flex>
          <div className="landing-hero-shot">
            <Browser src={shotResults} alt="Research Studio 中一次完成实验的结果页" />
          </div>
          <Grid columns={{ initial: "2", md: "4" }} gap="3" className="landing-stats">
            {STATS.map(([value, label]) => (
              <div key={label} className="landing-stat">
                <strong>{value}</strong>
                <span>{label}</span>
              </div>
            ))}
          </Grid>
        </div>
      </section>

      <main>
        <section id="overview" className="landing-section">
          <div className="landing-wrap">
            <SectionTitle eyebrow="OVERVIEW" title="从个体行为到集体涌现">
              TwinMarket 研究个体行为如何通过互动与反馈机制，汇聚成金融市场中的集体动态与涌现现象。
            </SectionTitle>
            <Grid columns={{ initial: "1", sm: "2" }} gap="4" mb="6">
              <FeatureCard title="🎯 核心问题">
                传统基于规则的 ABM 难以刻画人类行为的多样性，尤其是行为经济学中的非理性因素。
              </FeatureCard>
              <FeatureCard title="⚡ 我们的方案">
                由 LLM 驱动的 Agent 兼顾认知偏差、情绪波动与非理性影响，在模拟股市中自然产生
                <mark>金融泡沫</mark>、<mark>市场衰退</mark>等集体现象。
              </FeatureCard>
            </Grid>
            <Figure src={overviewFigure} alt="TwinMarket 总览" caption="TwinMarket 总览：真实用户画像驱动的 Agent 在社交网络与订单驱动市场中互动。" />
            <Box mt="6">
              <Text as="p" size="2" color="gray" mb="3">
                论文作者
              </Text>
              <Text as="p" size="3" className="landing-authors">
                {AUTHORS.map(([name, mark], index) => (
                  <span key={name}>
                    {name}
                    <sup>{mark}</sup>
                    {index < AUTHORS.length - 1 ? "，" : ""}
                  </span>
                ))}
              </Text>
              <Text as="p" size="1" color="gray" mt="1">
                * 共同一作　‡ 共同指导
              </Text>
              <Flex gap="5" wrap="wrap" align="center" mt="3">
                <Flex gap="2" align="center">
                  <sup>1</sup>
                  <img src={cuhkLogo} alt="" className="landing-affil" />
                  <Text size="2">香港中文大学（深圳）</Text>
                </Flex>
                <Flex gap="2" align="center">
                  <sup>2</sup>
                  <img src={njuLogo} alt="" className="landing-affil" />
                  <Text size="2">南京大学</Text>
                </Flex>
              </Flex>
            </Box>
          </div>
        </section>

        <section id="framework" className="landing-section landing-alt">
          <div className="landing-wrap">
            <SectionTitle eyebrow="FRAMEWORK" title="微观决策 × 宏观市场">
              微观层用 BDI 框架模拟每个用户的决策，宏观层模拟订单驱动的撮合交易与社交信息传播，两层都以真实行为数据、行情、新闻与公告为依据。
            </SectionTitle>
            <Figure src={frameworkFigure} alt="TwinMarket 框架" />
            <Grid columns={{ initial: "1", sm: "2" }} gap="4" mt="6">
              <FeatureCard title="🧠 BDI 决策">
                <ul>
                  <li>
                    <b>Belief</b>：Agent 对市场的理解
                  </li>
                  <li>
                    <b>Desire</b>：Agent 的目标与偏好
                  </li>
                  <li>
                    <b>Intention</b>：具体的交易行为
                  </li>
                </ul>
              </FeatureCard>
              <FeatureCard title="📊 行为偏差">
                <Flex gap="2" wrap="wrap" my="2">
                  {["过度自信", "损失厌恶", "羊群效应", "风险偏好"].map((tag) => (
                    <Badge key={tag} variant="soft" radius="full">
                      {tag}
                    </Badge>
                  ))}
                </Flex>
                影响交易决策，带来市场参与者的异质性。
              </FeatureCard>
              <FeatureCard title="🌐 动态社交网络">
                <ul>
                  <li>网络随交易模式演化</li>
                  <li>策略相似的 Agent 相互连接</li>
                  <li>近期互动权重更高</li>
                </ul>
              </FeatureCard>
              <FeatureCard title="📡 信息传播">
                <ul>
                  <li>从社交关系中聚合信息</li>
                  <li>意见领袖塑造市场情绪</li>
                  <li>形成观点极化的回音室</li>
                </ul>
              </FeatureCard>
            </Grid>
            <Heading as="h3" size="4" mt="7" mb="4">
              数据来源
            </Heading>
            <Grid columns={{ initial: "2", md: "4" }} gap="3" mb="5">
              {[
                ["100K+", "雪球真实用户画像"],
                ["1M+", "历史交易明细"],
                ["50", "CSMAR 上证 50 行情"],
                ["每日", "新浪财经、同花顺、巨潮公告"],
              ].map(([value, label]) => (
                <div key={label} className="landing-stat">
                  <strong>{value}</strong>
                  <span>{label}</span>
                </div>
              ))}
            </Grid>
            <Figure src={dataFigure} alt="数据来源" />
          </div>
        </section>

        <section id="results" className="landing-section">
          <div className="landing-wrap">
            <SectionTitle eyebrow="RESULTS" title="模拟市场复现真实规律">
              模拟中涌现出意见领袖与信息极化；谣言使信念分化、卖出意愿上升，最终引发市场急跌。
            </SectionTitle>
            <Figure src={rumorFigure} alt="谣言下的行为极化" caption="谣言冲击下的行为极化与卖买比变化。" />
            <Heading as="h3" size="4" mt="7" mb="4">
              市场典型事实
            </Heading>
            <Grid columns={{ initial: "1", sm: "2", md: "4" }} gap="3" mb="5">
              <FeatureCard title="📊 收益率厚尾">极端价格变动比正态分布更频繁。</FeatureCard>
              <FeatureCard title="📈 波动聚集">高波动时期之后往往仍是高波动。</FeatureCard>
              <FeatureCard title="⚖️ 杠杆效应">负收益与未来波动上升相关。</FeatureCard>
              <FeatureCard title="💹 量价关系">成交量与价格变动正相关。</FeatureCard>
            </Grid>
            <Figure src={stylizedFigure} alt="市场典型事实" />
            <Grid columns={{ initial: "1", md: "2" }} gap="6" mt="7" align="center">
              <Box>
                <Heading as="h3" size="4" mb="3">
                  可扩展性
                </Heading>
                <Text as="p" size="3" color="gray">
                  激活交易者比例从 10% 提升到 80%，平均与峰值成交量随之上升，预测误差（RMSE / MAE）下降。扩展到 1,000 个 Agent 时，模拟指数仍能较好地追踪真实指数走势。
                </Text>
              </Box>
              <Figure src={indexFigure} alt="1000 个 Agent 的模拟指数与真实指数" />
            </Grid>
          </div>
        </section>

        <section id="studio" className="landing-section landing-studio">
          <div className="landing-wrap">
            <SectionTitle eyebrow="TWINMARKET UI" title="Research Studio：在浏览器里做市场模拟研究">
              Research Studio 是 TwinMarket 的本地研究工作台。它连接独立的后端 Runtime API，把模拟的配置、运行、监控与结果分析放进同一个界面，实验数据与结果都保存在本机。
            </SectionTitle>

            <Grid columns={{ initial: "1", sm: "2", md: "4" }} gap="3" mb="7">
              {WORKFLOW.map(([title, text], index) => (
                <div key={title} className="landing-step">
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <Heading as="h4" size="3" my="1">
                    {title}
                  </Heading>
                  <Text as="p" size="2" color="gray">
                    {text}
                  </Text>
                </div>
              ))}
            </Grid>

            <Flex justify="center" mb="4">
              <SegmentedControl.Root value={shot} onValueChange={setShot} aria-label="界面预览" className="landing-shot-tabs">
                {SHOTS.map(([id, label]) => (
                  <SegmentedControl.Item key={id} value={id}>
                    {label}
                  </SegmentedControl.Item>
                ))}
              </SegmentedControl.Root>
            </Flex>
            <Browser src={current[2]} alt={`Research Studio · ${current[1]}`} />

            <Grid columns={{ initial: "1", sm: "2" }} gap="4" mt="7">
              {SPACES.map((space) => (
                <a key={space.id} href={`/studio/${space.id}`} onClick={open(`/studio/${space.id}`)} className="landing-space">
                  <span className="landing-space-icon">{icon(space.icon, 18)}</span>
                  <span>
                    <strong>
                      {space.name} {icon(ArrowRightIcon, 14)}
                    </strong>
                    <span>{space.text}</span>
                  </span>
                </a>
              ))}
            </Grid>

            <Grid columns={{ initial: "1", md: "3" }} gap="4" mt="6">
              <FeatureCard title="🔁 真实的本地模拟闭环">
                实验在后台进程中按交易日推进，每一步写入持久化的运行事件；停止后可从最近的检查点续跑。
              </FeatureCard>
              <FeatureCard title="🗂️ 可复现的市场快照">
                通过 Tushare 增量同步或回补收盘行情，发布带质量报告的不可变快照，实验记录所用快照。
              </FeatureCard>
              <FeatureCard title="📤 结果可导出">
                每日汇总、成交明细、委托、Agent 结果与社交数据都能在页面查看，并导出为 CSV 进一步分析。
              </FeatureCard>
            </Grid>

            <Box mt="7" className="landing-cta">
              <Flex direction={{ initial: "column", sm: "row" }} gap="4" align={{ initial: "start", sm: "center" }} justify="between">
                <Box>
                  <Heading as="h3" size="5">
                    开始你的第一次模拟
                  </Heading>
                  <Text as="p" size="2" color="gray" mt="1">
                    创建实验、选择 Agent 规模与区间，观察市场如何从个体决策中涌现。
                  </Text>
                </Box>
                <Flex gap="3" wrap="wrap">
                  <Button asChild size="3">
                    <a href="/studio/Experiment/new" onClick={open("/studio/Experiment/new")}>
                      {icon(RocketIcon)} 创建实验
                    </a>
                  </Button>
                  <Button asChild size="3" variant="soft" color="gray">
                    <a href="/studio/Documentation" onClick={open("/studio/Documentation")}>
                      {icon(BarChartIcon)} 阅读文档
                    </a>
                  </Button>
                </Flex>
              </Flex>
            </Box>
          </div>
        </section>

        <section id="citation" className="landing-section landing-alt">
          <div className="landing-wrap">
            <SectionTitle eyebrow="CITATION" title="引用">
              如果 TwinMarket 对你的研究有帮助，请引用以下论文。
            </SectionTitle>
            <Citation />
            <Flex gap="3" wrap="wrap" mt="4">
              <Button asChild variant="soft" color="gray">
                <a href={PAPER.poster} target="_blank" rel="noreferrer">
                  🖼️ Poster
                </a>
              </Button>
              <Button asChild variant="soft" color="gray">
                <a href={PAPER.talk} target="_blank" rel="noreferrer">
                  🎤 Talk
                </a>
              </Button>
            </Flex>
          </div>
        </section>
      </main>

      <footer className="landing-footer">
        <div className="landing-wrap">
          <Text size="1" color="gray">
            © 2025 TwinMarket Project · CUHK-Shenzhen & Nanjing University
          </Text>
        </div>
      </footer>
    </div>
  );
}
