# gpu-dev usage by user - last 90 days (2026-05-13 to 2026-08-11 UTC)

43 distinct users, 41,918 GPU-hours, 2,165 CPU node-hours, 2,043 reservations.

Top 10 users account for 86% of all GPU-hours.

GPU-hours = gpu_count x hours-live, credited only for the part of each reservation inside the window. CPU pods carry 0 GPUs and are counted as node-hours. MIG slices count as 1 whole GPU (single-digit hours either way).

Source: DynamoDB pytorch-gpu-dev-reservations in us-east-2 (prod, 99.6% of activity), us-east-1 (spot) and us-west-1 (staging). meta_user is the SSO role-session identity the reservation was authenticated with.

Generated 2026-08-11T18:41:54Z by scripts/gpu_dev_users.py.

| # | github_user | meta_user | GPU-hours | CPU node-hours | wall-hours | reservations | last active | GPU mix (GPU-hours) |
|--:|---|---|--:|--:|--:|--:|---|---|
| 1 | tarinduj | tarindu | 8,833.7 | 0.0 | 1,618.2 | 48 | 2026-08-11 | h100 7510.9, b200 1298.8, a100 24 |
| 2 | bobrenjc93 | bobren | 8,472.3 | 17.9 | 1,257.7 | 970 | 2026-07-30 | h100 8454.9, l4 10.3, a100 7.1 |
| 3 | eellison | eellison | 4,150.6 | 0.0 | 1,328.0 | 34 | 2026-08-11 | b200 4150.6 |
| 4 | iamzainhuda | zainhuda | 2,895.1 | 0.0 | 723.8 | 24 | 2026-08-07 | h100 2894.1, h100-mig-1g 1 |
| 5 | calebmkim | calebkim | 2,847.9 | 25.2 | 2,345.2 | 82 | 2026-08-11 | b200 2010, h100 830.9, b200-mig-1g 7 |
| 6 | yushangdi | shangdiy | 2,128.2 | 0.0 | 1,932.0 | 55 | 2026-08-07 | h100 1106.7, b200 1013.4, a10g 8 |
| 7 | amesingflank | dunfanlu | 2,113.8 | 0.0 | 1,474.6 | 35 | 2026-08-11 | b200 1432.5, h100 681.3 |
| 8 | vkuzo | vasiliy | 1,977.5 | 0.0 | 1,785.9 | 66 | 2026-08-11 | b200 1186.4, h100 791.1 |
| 9 | drisspg | drisspg | 1,536.1 | 0.0 | 1,464.2 | 114 | 2026-08-11 | b200 1535.5, h100 0.5, b200-mig-1g 0.1, h100-mig-1g 0 |
| 10 | d4l3k | tristanr | 1,240.8 | 0.0 | 302.5 | 23 | 2026-07-31 | h100 1240.3, a100 0.5 |
| 11 | felipemello1 | felipemello | 872.7 | 0.0 | 436.3 | 12 | 2026-08-11 | h100 872.7 |
| 12 | slayton58 | simonlayton | 834.8 | 0.0 | 497.8 | 64 | 2026-08-11 | b200 821.7, b200-mig-1g 9, a100 4 |
| 13 | gmagogsfm | ycao | 792.7 | 0.0 | 552.5 | 12 | 2026-06-07 | h100 744.7, b200 48 |
| 14 | xmfan | xmfan | 660.1 | 0.0 | 172.1 | 23 | 2026-08-05 | h100 507.1, b200 152.9 |
| 15 | ezyang | ezyang | 430.4 | 55.7 | 486.1 | 41 | 2026-08-11 | h100 430.4 |
| 16 | karthickai | karthickps | 291.9 | 0.0 | 291.9 | 36 | 2026-08-11 | b200 291.9 |
| 17 | danielvegamyhre | danvm | 194.4 | 0.0 | 122.3 | 5 | 2026-05-29 | b200 146.3, b200-mig-1g 48.1 |
| 18 | angelayi | angelayi | 192.3 | 0.0 | 192.3 | 7 | 2026-07-02 | l4 192.3 |
| 19 | wdvr | wouterdevriendt | 165.5 | 1,540.8 | 1,684.1 | 255 | 2026-08-11 | b200 50.1, t4 49.9, l4 33.1, h100 23.4 |
| 20 | atalman | atalman | 164.0 | 0.0 | 164.0 | 21 | 2026-07-14 | h100 95.5, b200 48.5, b200-mig-1g 20.1 |
| 21 | choijon5 | jongsokchoi | 149.0 | 0.0 | 76.6 | 8 | 2026-06-11 | b200 120.6, h100 28.4 |
| 22 | gchanan | gchanan | 144.3 | 0.0 | 72.1 | 3 | 2026-06-12 | b200 144.3 |
| 23 | aditvenk | avenkataraman | 144.2 | 0.0 | 48.1 | 2 | 2026-07-22 | b200 144.2 |
| 24 | xuzhao9 | xzhao9 | 81.1 | 0.0 | 38.0 | 4 | 2026-07-23 | b200 69.5, h100 11.6 |
| 25 | alirezashamsoshoara | alisol | 73.2 | 0.0 | 9.1 | 3 | 2026-08-09 | h100 73.2 |
| 26 | zou3519 | rzou | 72.2 | 0.0 | 40.1 | 3 | 2026-06-26 | l4 48.1, b200 24.1 |
| 27 | larryliu0820 | larryliu | 72.1 | 0.0 | 72.1 | 3 | 2026-05-19 | a100 72.1 |
| 28 | malfet | nshulga | 69.5 | 0.0 | 69.5 | 9 | 2026-06-19 | b200 64.3, b200-mig-1g 5.2 |
| 29 | huydhn | huydo | 62.3 | 3.0 | 65.4 | 9 | 2026-08-06 | h100 50.2, a10g 8.5, h100-mig-1g 3.7 |
| 30 | shunting314 | shunting | 48.1 | 0.0 | 24.0 | 1 | 2026-05-20 | b200 48.1 |
| 31 | janeyx99 | janeyx | 48.0 | 0.0 | 48.0 | 1 | 2026-07-01 | b200 48 |
| 32 | ryanzhang22 | ryanzhang | 42.4 | 0.0 | 34.3 | 5 | 2026-08-11 | h100 42.4 |
| 33 | yipjustin | yipjustin | 24.0 | 0.0 | 24.0 | 1 | 2026-05-19 | b200 24 |
| 34 | colesbury | sgross | 23.3 | 0.0 | 23.3 | 1 | 2026-05-14 | h100 23.3 |
| 35 | wwwjn | jianiw | 16.2 | 0.0 | 8.1 | 2 | 2026-07-16 | t4 16.2 |
| 36 | tushar00jain | tushar00jain | 16.1 | 0.0 | 8.0 | 1 | 2026-06-03 | b200 16.1 |
| 37 | frgossen | frgossen | 16.1 | 0.0 | 16.1 | 2 | 2026-06-24 | t4 16.1 |
| 38 | aorenste | aorenste | 14.2 | 0.0 | 14.2 | 48 | 2026-07-29 | h100 11.5, t4 1.4, a10g 1.1, a100 0.2 |
| 39 | paulzhang12 | paulzhan | 4.0 | 0.0 | 4.0 | 1 | 2026-06-09 | h100 4 |
| 40 | desertfire | binbao | 2.9 | 0.0 | 2.9 | 5 | 2026-08-06 | h100 2.9 |
| 41 | alband | albandes | 0.2 | 0.4 | 0.6 | 2 | 2026-07-26 | l4 0.2 |
| 42 | zhxchen17 | zhxchen17 | 0.0 | 522.3 | 522.3 | 1 | 2026-06-04 |  |
| 43 | jathu | jathu | 0.0 | 0.1 | 0.1 | 1 | 2026-07-02 |  |
