import bluesky as bs
import gym
import numpy as np
from bluesky.stack import simstack
from bluesky.tools.aero import (metres_to_feet_rounded,
                                metric_spd_to_knots_rounded)
from bluesky.tools.geo import latlondist, qdrpos
from gym import spaces
from matplotlib import pyplot as plt
from mycolorpy import colorlist as mcp

from tud_rl.envs._envs.VesselFnc import NM_to_meter

COLORS = [plt.rcParams["axes.prop_cycle"].by_key()["color"][i] for i in range(8)] + 5 * mcp.gen_color(cmap="tab20b", n=20) 


class BlueSky_Env(gym.Env):
    """Aircraft simulation env based on the BlueSky simulator of Ellerbroek and Hoekstra."""
    def __init__(self):
        super(BlueSky_Env, self).__init__()

        # flight params
        self.ac_alt = 12000 # ft
        self.ac_spd = 250 # kts
        self.ac_type = "A320"

        # viz params
        self.LoS_pts = 50
        self.clock_degs = np.linspace(0.0, 360.0, num=self.LoS_pts, endpoint=False)

        # config
        obs_size = 1
        self.observation_space = spaces.Box(low  = np.full(obs_size, -np.inf, dtype=np.float32), 
                                            high = np.full(obs_size,  np.inf, dtype=np.float32))
        self.action_space = spaces.Discrete(3)

        self.delta_t = 0.5
        self._max_episode_steps = 500

        # initialize BlueSky simulator
        bs.init(mode='sim', detached=True)

        # activate CD&R with only horizontal MVP resolution
        bs.stack.stack("ASAS ON")
        bs.stack.stack("RESO MVP")
        bs.stack.stack("RMETHV OFF")
        bs.stack.stack("RMETHH HDG")

        # set standard values for LoS (radius 5nm, 1000ft half vertical distance)
        self.LoS_dist = 5 # nm
        bs.stack.stack("RSZONEH 1000")
        bs.stack.stack(f"RSZONER {self.LoS_dist}")

        # set simulation time
        bs.stack.stack(f"DT {self.delta_t}")
        simstack.process()


    def reset(self):
        """Resets environment to initial state."""
        self.step_cnt = 0           # simulation step counter
        self.sim_t    = 0           # overall passed simulation time (in s)
        self.LoSs_all = 0           # episode-wise LoSs
        self.LoSs_cnt = 0           # step-wise LoSs

        # useful commands:
        # CRE acid, type, lat, lon, hdg, alt (FL or ft), spd (kts)
        # CRECONF acid, type, targetid, dpsi, cpa, tlosh, spd
        # ADDWPT acid, (wpname/lat,lon),[alt],[spd],[afterwp],[beforewp]

        # place some aircrafts
        bs.stack.stack(f"CRE 002, {self.ac_type}, 9.7, 10.0, 0, {self.ac_alt}, {self.ac_spd}")
        simstack.process()

        # create some aircrafts in conflict
        for i in range(0, 5):#, "008", "009"]
            bs.stack.stack(f"CRECONFS {'I' + str(i)}, {self.ac_type}, 002, {360 * i/30}, {self.LoS_dist*0.2}, 300, , ,{self.ac_spd}")

        # turn on LNAV, turn off VNAV (we stay at one altitude)
        for acid in bs.traf.id:
            bs.stack.stack(f"LNAV {acid}, ON")
            bs.stack.stack(f"VNAV {acid}, OFF")
        simstack.process()

        # set linear waypoints
        for i, acid in enumerate(bs.traf.id):
            wp_lat, wp_lon = qdrpos(latd1=bs.traf.lat[i], lond1=bs.traf.lon[i], qdr=bs.traf.hdg[i], dist=100)
            bs.stack.stack(f"ADDWPT {acid}, {wp_lat}, {wp_lon}, {self.ac_alt}, {self.ac_spd}")
        simstack.process()

        # init state
        self._set_state()
        self.state_init = self.state
        return self.state
  
    def _set_state(self):
        self.state = None

    def step(self, a):
        # increase step cnt and overall simulation time
        self.step_cnt += 1
        self.sim_t += self.delta_t
 
        # update simulator
        bs.sim.step()

        # LoSs
        self.LoSs_cnt = self._get_LoSs()
        self.LoSs_all += self.LoSs_cnt

        # compute state, reward, done        
        self._set_state()
        self._calculate_reward(a)
        d = self._done()
        return self.state, self.r, d, {}
 
    def _calculate_reward(self, a):
        self.r = 0.0

    def _done(self):
        return False

    def _get_LoSs(self):
        """Computes current Loss of Separations (LoSs)."""
        LoSs = 0
        for i in range(bs.traf.ntraf):
            for j in range(bs.traf.ntraf):
                if i < j:
                    if latlondist(latd1=bs.traf.lat[i], lond1=bs.traf.lon[i], latd2=bs.traf.lat[j], lond2=bs.traf.lon[j]) <= \
                        NM_to_meter(self.LoS_dist):
                        LoSs += 1
        return LoSs

    def __str__(self):
        return f"Step: {self.step_cnt}, Sim-Time [s]: {int(self.sim_t)}, Cnt LoSs: {self.LoSs_cnt}, All LoSs: {self.LoSs_all}"

    def render(self, mode=None):
        """Renders the current environment."""

        # plot every nth timestep
        if self.step_cnt % 10 == 0: 
            
            # init figure
            if len(plt.get_fignums()) == 0:
                self.f, self.ax1 = plt.subplots(1, 1, figsize=(10, 10))
                plt.ion()
                plt.show()           
            
            # set screen
            self.ax1.clear()
            self.ax1.set_xlim(8, 12)
            self.ax1.set_ylim(8, 12)
            self.ax1.set_xlabel("Lon [°]")
            self.ax1.set_ylabel("Lat [°]")
            self.ax1.text(0.125, 0.8875, self.__str__(), fontsize=9, transform=plt.gcf().transFigure)

            for i, acid in enumerate(bs.traf.id):
                # show aircraft
                self.ax1.scatter(bs.traf.lon[i], bs.traf.lat[i], marker=(3, 0, -bs.traf.hdg[i]), color=COLORS[i])

                # LoS area
                lats, lons = map(list, zip(*[qdrpos(latd1=bs.traf.lat[i], lond1=bs.traf.lon[i], 
                                                    qdr=deg, dist=self.LoS_dist) for deg in self.clock_degs]))
                self.ax1.plot(lons, lats, color=COLORS[i])

                # information
                s = f"ID:  {acid}" + "\n" +\
                    f"hdg: {bs.traf.hdg[i]:.1f}" + "\n" +\
                    f"alt: {metres_to_feet_rounded(bs.traf.alt[i])}" + "\n" +\
                    f"spd: {metric_spd_to_knots_rounded(bs.traf.cas[i])}"
                self.ax1.text(x=bs.traf.lon[i], y=bs.traf.lat[i], s=s, color=COLORS[i], fontdict={"size" : 8})
                
                # waypoints
                self.ax1.scatter(bs.traf.actwp.lon[i], bs.traf.actwp.lat[i], color=COLORS[i])

            plt.pause(0.001)